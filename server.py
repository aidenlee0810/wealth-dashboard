#!/usr/bin/env python3
"""
Wealth Dashboard — Local Proxy Server
  - 정적 파일: http://localhost:5500
  - Claude API 프록시: POST /api/claude  →  api.anthropic.com
  - FRED 프록시:       GET  /api/fred    →  api.stlouisfed.org  (CORS 우회)
  - 야후 캔들 프록시:  GET  /api/yhfin   →  query1.finance.yahoo.com
설치 불필요 (Python 3 기본 라이브러리만 사용)

Phase 1.1 보안 강화:
  - CORS: DELETE 추가, /api/local/* 는 localhost Origin 한정
  - CSRF: Origin/Referer 검사 + X-Local-Token 지원
  - limit clamp: max(1, min(parsed, 1000))
  - column allowlist: POST payload 필드 검증
"""
import http.server, urllib.request, urllib.error, urllib.parse, json, os, sys
import xml.etree.ElementTree as ET, email.utils, time, uuid, secrets
from pathlib import Path

PORT = int(os.environ.get('PORT', 5500))

# Lazy import of db module
def _get_db():
    try:
        import db as _db
        return _db
    except Exception as e:
        print(f"[warn] db module import failed: {e}")
        return None

# ── Rate limiting ────────────────────────────────────────────────────────────
_snapshot_last_run_ts = 0
SNAPSHOT_RATE_LIMIT_SEC = 3600  # 1 hour

# ── X-Local-Token for CSRF protection ────────────────────────────────────────
_local_token: str = ""

def _get_local_token() -> str:
    """Return server-side local token (generate once, persist to disk)."""
    global _local_token
    if _local_token:
        return _local_token
    token_file = Path.home() / ".wealth-dashboard" / "local_token"
    token_file.parent.mkdir(parents=True, exist_ok=True)
    if token_file.exists():
        _local_token = token_file.read_text().strip()
    if not _local_token or len(_local_token) < 32:
        _local_token = secrets.token_hex(32)
        token_file.write_text(_local_token)
        try:
            os.chmod(token_file, 0o600)  # owner read-only
        except OSError:
            pass
    return _local_token


# ── Live-quote (Alpaca) credentials + tiny response cache ────────────────────
# Keys stay SERVER-SIDE. The browser hits /api/price/* and never sees the key.
# Resolution order: process env → ~/.wealth-dashboard/secrets.env (gitignored,
# outside the repo). Accepts both our names and Alpaca's native env names.
_price_cache: dict = {}            # cache_key -> (epoch_ts, payload)
PRICE_CACHE_TTL_SEC = 15           # snapshots: ~real-time but de-bounced
CLOCK_CACHE_TTL_SEC = 30           # market clock changes slowly


def _parse_env_file(path: Path) -> dict:
    """Parse a simple KEY=VALUE .env file (ignores blanks/#comments)."""
    out: dict = {}
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def _provider_key(name: str) -> str:
    """Read a provider key from process env or ~/.wealth-dashboard/secrets.env."""
    value = os.environ.get(name, "")
    if value:
        return value
    return _parse_env_file(Path.home() / '.wealth-dashboard' / 'secrets.env').get(name, "")


def _alpaca_creds() -> tuple:
    """Return (key, secret) or (None, None). Env first, then secrets.env."""
    key = os.environ.get('ALPACA_KEY') or os.environ.get('APCA_API_KEY_ID')
    secret = os.environ.get('ALPACA_SECRET') or os.environ.get('APCA_API_SECRET_KEY')
    if key and secret:
        return key, secret
    env = _parse_env_file(Path.home() / '.wealth-dashboard' / 'secrets.env')
    key = key or env.get('ALPACA_KEY') or env.get('APCA_API_KEY_ID')
    secret = secret or env.get('ALPACA_SECRET') or env.get('APCA_API_SECRET_KEY')
    return (key, secret) if (key and secret) else (None, None)


def _get_live_quotes():
    """Lazy import of jobs/live_quotes.py (stdlib module)."""
    try:
        from jobs import live_quotes  # package import (jobs/__init__.py present)
        return live_quotes
    except Exception:
        jp = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'jobs')
        if jp not in sys.path:
            sys.path.insert(0, jp)
        import live_quotes  # type: ignore
        return live_quotes


# ── On-demand SEC fundamentals (any ticker, no key) — 24h cache ──────────────
_fund_cache: dict = {}             # TICKER -> (epoch_ts, item)
FUND_CACHE_TTL_SEC = 86400         # SEC companyfacts update quarterly


def _get_sec_fundamentals():
    """Lazy import of jobs/sec_fundamentals.py (reuses Fetcher + financials)."""
    try:
        from jobs import sec_fundamentals
        return sec_fundamentals
    except Exception:
        jp = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'jobs')
        if jp not in sys.path:
            sys.path.insert(0, jp)
        import sec_fundamentals  # type: ignore
        return sec_fundamentals


# ── Per-table column allowlists for /api/local/* POST payload ────────────────
LOCAL_TABLE_COLUMN_ALLOWLIST: dict[str, set] = {
    'portfolio_snapshots': {
        'date', 'ticker', 'account', 'shares', 'avg_cost', 'cost_basis',
        'current_price', 'current_value', 'weight', 'sector', 'themes_json',
        'risk_bucket', 'tax_lot_ref', 'source',
    },
    'user_target_weights': {
        'ticker', 'account', 'target_weight', 'target_amount', 'rationale', 'notes',
    },
    'user_actions': {
        'action_id', 'date', 'signal_id', 'ticker', 'action_type', 'actual_price',
        'actual_amount', 'actual_shares', 'account', 'notes',
    },
    'account_buckets': {
        'account_name', 'monthly_budget', 'tax_status', 'restrictions_json',
    },
    'tax_lots': {
        'lot_id', 'ticker', 'account', 'qty', 'cost_basis_per_share', 'total_cost',
        'acquired_date', 'lt_eligible_date', 'sold_date', 'sold_price',
        'realized_gain', 'status',
    },
    'personal_notes': {
        'note_id', 'ticker', 'date', 'text', 'tags_json',
    },
    'allocation_outputs': {
        'date', 'ticker', 'signal_id', 'ledger', 'suggested_size_before_risk',
        'suggested_size_after_risk', 'actual_size', 'account', 'risk_status',
        'risk_flags_json', 'sizing_method', 'notes',
    },
}

# Allowed origin values for private (local) endpoints
_ALLOWED_LOCAL_ORIGINS = frozenset({
    'http://localhost:5500',
    'http://127.0.0.1:5500',
})


class Handler(http.server.SimpleHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(200)
        # Decide CORS policy based on path
        if self.path.startswith('/api/local/') or self.path == '/api/snapshot':
            self._cors_local()
        else:
            self._cors_public()
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == '/api/fred':
            self._proxy_fred(parsed.query)
        elif parsed.path == '/api/finnhub':
            self._proxy_finnhub(parsed.query)
        elif parsed.path == '/api/yhfin':
            self._proxy_yhfin(parsed.query)
        elif parsed.path.startswith('/api/edgar'):
            self._proxy_edgar(parsed.path, parsed.query)
        elif parsed.path == '/api/feargreed':
            self._proxy_feargreed()
        elif parsed.path == '/api/news':
            self._proxy_news(parsed.query)
        elif parsed.path == '/api/price/snapshot':
            self._proxy_price_snapshot(parsed.query)
        elif parsed.path == '/api/price/clock':
            self._proxy_price_clock()
        elif parsed.path == '/api/fundamentals':
            self._proxy_fundamentals(parsed.query)
        elif parsed.path == '/api/db/health':
            self._db_health()
        elif parsed.path == '/api/db/query':
            self._db_query(parsed.query)
        elif parsed.path.startswith('/api/local/'):
            self._local_handler('GET', parsed.path[len('/api/local/'):], parsed.query, body=None)
        else:
            super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == '/api/claude':
            self._proxy_claude()
            return
        if parsed.path == '/api/snapshot':
            self._handle_snapshot()
            return
        if parsed.path.startswith('/api/local/'):
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length) if length else b''
            self._local_handler('POST', parsed.path[len('/api/local/'):], parsed.query, body=body)
            return
        super().do_POST()

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith('/api/local/'):
            self._local_handler('DELETE', parsed.path[len('/api/local/'):], parsed.query, body=None)
            return
        self.send_response(405)
        self._cors_public()
        self.end_headers()

    # ── CSRF / origin check ──────────────────────────────────────────────────
    def _check_local_access(self) -> tuple[bool, str]:
        """Two-layer CSRF guard for /api/local/* and /api/snapshot.

        Layer 1: IP must be localhost.
        Layer 2: Origin header, if present, must be a known-safe localhost origin.
                 Absent Origin (curl/local script) is allowed.
        Layer 3 (optional): X-Local-Token must match if provided.

        Returns (allowed, reason_if_denied).
        """
        client_ip = self.client_address[0]
        if client_ip not in ('127.0.0.1', '::1', 'localhost'):
            return False, 'remote IP not allowed'

        origin = self.headers.get('Origin', '').strip()
        if origin and origin not in _ALLOWED_LOCAL_ORIGINS:
            return False, f'external Origin blocked: {origin}'

        # Optional token validation (forward-compatible)
        provided_token = self.headers.get('X-Local-Token', '').strip()
        if provided_token:
            if provided_token != _get_local_token():
                return False, 'invalid X-Local-Token'

        return True, 'ok'

    # ── Proxy helpers ────────────────────────────────────────────────────────
    def _proxy_claude(self):
        length = int(self.headers.get('Content-Length', 0))
        body   = self.rfile.read(length)
        key    = self.headers.get('x-api-key', '')
        req = urllib.request.Request(
            'https://api.anthropic.com/v1/messages',
            data=body,
            headers={
                'Content-Type':      'application/json',
                'x-api-key':         key,
                'anthropic-version': '2023-06-01',
            },
            method='POST',
        )
        try:
            with urllib.request.urlopen(req) as r:
                data = r.read()
            self.send_response(200)
        except urllib.error.HTTPError as e:
            data = e.read()
            self.send_response(e.code)
        self._cors_public()
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(data)

    # ── FRED proxy ──────────────────────────────────────────────────────────
    def _proxy_fred(self, query):
        params = urllib.parse.parse_qs(query)
        series_id = params.get('series_id', [''])[0]
        api_key   = params.get('api_key', [''])[0] or _provider_key('FRED_KEY')
        limit     = params.get('limit', ['260'])[0]
        sort      = params.get('sort_order', ['desc'])[0]

        if not series_id or not api_key:
            self._json_error(400, 'series_id and api_key required')
            return

        fred_url = (
            f'https://api.stlouisfed.org/fred/series/observations'
            f'?series_id={urllib.parse.quote(series_id)}'
            f'&api_key={urllib.parse.quote(api_key)}'
            f'&file_type=json&sort_order={sort}&limit={limit}'
        )
        try:
            req = urllib.request.Request(fred_url, headers={'User-Agent': 'WealthDashboard/1.0'})
            with urllib.request.urlopen(req, timeout=10) as r:
                data = r.read()
            self.send_response(200)
            self._cors_public()
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(data)
        except urllib.error.HTTPError as e:
            self._json_error(e.code, f'FRED error: {e.code}')
        except Exception as e:
            self._json_error(502, str(e))

    # ── Finnhub proxy (keeps FINNHUB_KEY server-side in Local Mode) ─────────
    def _proxy_finnhub(self, query):
        params = urllib.parse.parse_qs(query)
        path = (params.pop('path', [''])[0] or '').strip()
        api_key = params.pop('token', [''])[0] or _provider_key('FINNHUB_KEY')

        if not path.startswith('/') or '://' in path or '..' in path:
            self._json_error(400, 'valid Finnhub path required')
            return
        if not api_key:
            self._json_error(503, 'FINNHUB_KEY not configured')
            return

        query_string = urllib.parse.urlencode(
            {k: v[-1] for k, v in params.items() if v},
            doseq=False,
        )
        suffix = f"{query_string}&" if query_string else ""
        url = f"https://finnhub.io/api/v1{path}?{suffix}token={urllib.parse.quote(api_key)}"
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'WealthDashboard/1.0'})
            with urllib.request.urlopen(req, timeout=10) as r:
                data = r.read()
            self.send_response(200)
            self._cors_public()
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(data)
        except urllib.error.HTTPError as e:
            self._json_error(e.code, f'Finnhub error: {e.code}')
        except Exception as e:
            self._json_error(502, str(e))

    # ── Yahoo Finance candle proxy ──────────────────────────────────────────
    def _proxy_yhfin(self, query):
        params  = urllib.parse.parse_qs(query)
        symbol  = params.get('symbol', [''])[0]
        period  = params.get('period', ['1y'])[0]
        interval = params.get('interval', ['1d'])[0]

        if not symbol:
            self._json_error(400, 'symbol required')
            return

        yf_url = (
            f'https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}'
            f'?interval={interval}&range={period}&includePrePost=false'
        )
        try:
            req = urllib.request.Request(yf_url, headers={
                'User-Agent': 'Mozilla/5.0',
                'Accept': 'application/json',
            })
            with urllib.request.urlopen(req, timeout=15) as r:
                raw = json.loads(r.read())

            result = raw.get('chart', {}).get('result', [{}])[0]
            timestamps = result.get('timestamp', [])
            ohlcv = result.get('indicators', {}).get('quote', [{}])[0]
            adjclose = result.get('indicators', {}).get('adjclose', [{}])[0].get('adjclose', [])

            out = {
                's': 'ok',
                't': timestamps,
                'o': ohlcv.get('open', []),
                'h': ohlcv.get('high', []),
                'l': ohlcv.get('low', []),
                'c': ohlcv.get('close', []),
                'v': ohlcv.get('volume', []),
            }
            valid = [i for i, t in enumerate(timestamps) if t is not None and out['c'][i] is not None]
            for k in ['t','o','h','l','c','v']:
                out[k] = [out[k][i] for i in valid]

            self.send_response(200)
            self._cors_public()
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(out).encode())
        except urllib.error.HTTPError as e:
            self._json_error(e.code, f'Yahoo Finance error: {e.code}')
        except Exception as e:
            self._json_error(502, str(e))

    # ── SEC EDGAR proxy ─────────────────────────────────────────────────────
    def _proxy_edgar(self, path, query):
        edgar_path = path[len('/api/edgar'):]
        if not edgar_path:
            edgar_path = '/'
        qs = ('?' + query) if query else ''
        if edgar_path.startswith('/Archives') or edgar_path.startswith('/files/'):
            base = 'https://www.sec.gov'
        else:
            base = 'https://data.sec.gov'
        edgar_url = base + edgar_path + qs
        try:
            req = urllib.request.Request(edgar_url, headers={
                'User-Agent': 'WealthResearch/1.0 research@example.com',
                'Accept': 'application/json',
            })
            with urllib.request.urlopen(req, timeout=20) as r:
                data = r.read()
                content_type = r.headers.get('Content-Type', 'application/json')
            self.send_response(200)
            self._cors_public()
            self.send_header('Content-Type', content_type)
            self.end_headers()
            self.wfile.write(data)
        except urllib.error.HTTPError as e:
            self._json_error(e.code, f'EDGAR error: {e.code}')
        except Exception as e:
            self._json_error(502, str(e))

    # ── CNN Fear & Greed proxy ──────────────────────────────────────────────
    def _proxy_feargreed(self):
        urls = [
            'https://production.dataviz.cnn.io/index/fearandgreed/graphical',
            'https://production.dataviz.cnn.io/index/fearandgreed/current',
        ]
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://www.cnn.com/markets/fear-and-greed',
            'Origin': 'https://www.cnn.com',
        }
        for url in urls:
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=10) as r:
                    data = r.read()
                self.send_response(200)
                self._cors_public()
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(data)
                return
            except Exception:
                continue
        self._json_error(502, 'CNN Fear & Greed unavailable')

    # ── Free News RSS Aggregator ────────────────────────────────────────────
    def _proxy_news(self, query):
        params = urllib.parse.parse_qs(query)
        mode = params.get('mode', ['market'])[0]  # noqa: F841

        FEEDS = [
            ('CNBC',           'https://www.cnbc.com/id/100003114/device/rss/rss.html'),
            ('CNBC Markets',   'https://www.cnbc.com/id/20910258/device/rss/rss.html'),
            ('Reuters',        'https://feeds.reuters.com/reuters/businessNews'),
            ('AP News',        'https://feeds.apnews.com/apnews/finance'),
            ('MarketWatch',    'https://feeds.marketwatch.com/marketwatch/topstories/'),
            ('Yahoo Finance',  'https://finance.yahoo.com/news/rssindex'),
            ('Federal Reserve','https://www.federalreserve.gov/feeds/press_all.xml'),
        ]

        articles = []
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            'Accept': 'application/rss+xml, application/xml, text/xml, */*',
        }

        for source_name, url in FEEDS:
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=8) as r:
                    content = r.read()
                root = ET.fromstring(content)
                ns = {'atom': 'http://www.w3.org/2005/Atom'}
                items = root.findall('.//item') or root.findall('.//atom:entry', ns)
                for item in items[:8]:
                    title = (item.findtext('title') or item.findtext('atom:title', namespaces=ns) or '').strip()
                    link  = (item.findtext('link')  or item.findtext('atom:link', namespaces=ns) or '').strip()
                    if not link:
                        link_el = item.find('atom:link', ns)
                        link = link_el.get('href', '') if link_el is not None else ''
                    pub   = (item.findtext('pubDate') or item.findtext('atom:published', namespaces=ns) or '').strip()
                    desc  = (item.findtext('description') or item.findtext('atom:summary', namespaces=ns) or '').strip()
                    import re
                    desc = re.sub(r'<[^>]+>', '', desc)[:300]
                    if not title or not link:
                        continue
                    try:
                        ts = int(email.utils.parsedate_to_datetime(pub).timestamp())
                    except Exception:
                        ts = int(time.time())
                    articles.append({
                        'headline': title,
                        'source':   source_name,
                        'url':      link,
                        'summary':  desc,
                        'datetime': ts,
                    })
            except Exception:
                continue

        seen = set()
        unique = []
        for a in sorted(articles, key=lambda x: x['datetime'], reverse=True):
            key = a['headline'][:60].lower()
            if key not in seen:
                seen.add(key)
                unique.append(a)

        self.send_response(200)
        self._cors_public()
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(unique[:30]).encode())

    # ── DB Health ───────────────────────────────────────────────────────────
    def _db_health(self):
        _db = _get_db()
        if _db is None:
            self._json_error(503, 'DB module unavailable')
            return
        try:
            payload = _db.health()
            payload['mode'] = 'local'
            payload['snapshot_age_hours'] = None

            # Include local token ONLY if request is from localhost
            # (so external sites cannot read it via public health endpoint)
            client_ip = self.client_address[0]
            if client_ip in ('127.0.0.1', '::1', 'localhost'):
                payload['local_token'] = _get_local_token()

            if payload.get('last_snapshot_executed_at'):
                from datetime import datetime, timezone
                try:
                    snap = payload['last_snapshot_executed_at']
                    if isinstance(snap, str):
                        snap_dt = datetime.fromisoformat(snap.replace('Z', '+00:00'))
                    else:
                        snap_dt = snap
                    if snap_dt.tzinfo is None:
                        snap_dt = snap_dt.replace(tzinfo=timezone.utc)
                    age = (datetime.now(timezone.utc) - snap_dt).total_seconds() / 3600.0
                    payload['snapshot_age_hours'] = round(age, 2)
                except Exception:
                    pass
            self._json_ok(payload)
        except Exception as e:
            self._json_error(500, f'health error: {e}')

    # ── DB Query (cloud DB only, strict allowlist) ──────────────────────────
    def _db_query(self, query):
        _db = _get_db()
        if _db is None:
            self._json_error(503, 'DB module unavailable')
            return
        params = urllib.parse.parse_qs(query)
        table = params.get('table', [''])[0]
        if not table:
            self._json_error(400, 'table parameter required')
            return

        cols_raw = params.get('cols', [None])[0]
        cols = cols_raw.split(',') if cols_raw else None
        order_by = params.get('order_by', [None])[0]
        try:
            limit = int(params.get('limit', ['100'])[0])
        except ValueError:
            self._json_error(400, 'limit must be integer')
            return

        RESERVED = {'table', 'cols', 'order_by', 'limit'}
        where = {}
        for k, v in params.items():
            if k in RESERVED:
                continue
            where[k] = v[0]

        try:
            rows = _db.validate_and_query(
                table,
                cols=cols,
                where=where if where else None,
                order_by=order_by,
                limit=limit,
            )
            self._json_ok({'table': table, 'count': len(rows), 'rows': rows})
        except _db.QueryError as e:
            self._json_error(e.status, str(e))
        except Exception as e:
            self._json_error(500, f'query error: {e}')

    # ── Snapshot trigger (manual local run) ─────────────────────────────────
    def _handle_snapshot(self):
        global _snapshot_last_run_ts

        allowed, reason = self._check_local_access()
        if not allowed:
            self._json_error(403, f'snapshot access denied: {reason}')
            return

        now_ts = time.time()
        if now_ts - _snapshot_last_run_ts < SNAPSHOT_RATE_LIMIT_SEC:
            wait = int(SNAPSHOT_RATE_LIMIT_SEC - (now_ts - _snapshot_last_run_ts))
            self._json_error(429, f'rate limited; retry in {wait}s')
            return
        _snapshot_last_run_ts = now_ts

        self._json_ok({
            'status': 'pending',
            'message': 'snapshot endpoint stub — daily_snapshot.py implementation comes in Phase 3',
            'requested_at_utc': time.time(),
            'note': 'For now, run: python jobs/daily_snapshot.py manually',
        }, local=True)

    # ── Local personal DB handler (Local Mode only) ─────────────────────────
    def _local_handler(self, method, path, query, body):
        # Two-layer CSRF guard (IP + Origin + optional token)
        allowed, reason = self._check_local_access()
        if not allowed:
            self._json_error(403, f'/api/local/* access denied: {reason}')
            return

        _db = _get_db()
        if _db is None:
            self._json_error(503, 'DB module unavailable')
            return

        ALLOWED_LOCAL_RESOURCES = {
            'portfolio': 'portfolio_snapshots',
            'targets':   'user_target_weights',
            'actions':   'user_actions',
            'buckets':   'account_buckets',
            'lots':      'tax_lots',
            'notes':     'personal_notes',
            'allocations': 'allocation_outputs',
        }

        parts = path.strip('/').split('/')
        resource = parts[0] if parts else ''

        # Computed read-only resource: 3-ledger comparison (Plan §8, Phase 8).
        # Joins cloud signals/outcomes with local user_actions — Local Mode only.
        # Never persists; the result holds personal aggregates and must not be
        # written to a cloud view.
        if resource == 'ledger':
            if method != 'GET':
                self.send_response(405)
                self._cors_local()
                self.end_headers()
                return
            params = urllib.parse.parse_qs(query)
            try:
                horizon = int(params.get('horizon', ['60'])[0])
            except (ValueError, TypeError):
                self._json_error(400, 'horizon must be an integer')
                return
            if horizon not in (1, 5, 20, 60, 120):
                self._json_error(400, 'horizon must be one of 1/5/20/60/120')
                return
            account = params.get('account', [None])[0]
            try:
                import ledger_engine
                result = ledger_engine.compute_three_ledger(horizon, account)
                self._json_ok(result, local=True)
            except Exception as e:
                self._json_error(500, f'ledger error: {e}')
            return

        if resource not in ALLOWED_LOCAL_RESOURCES:
            self._json_error(404, f'unknown local resource: {resource}')
            return
        table = ALLOWED_LOCAL_RESOURCES[resource]

        CONFLICT_COLS = {
            'portfolio_snapshots': ['date', 'ticker', 'account'],
            'user_target_weights': ['ticker', 'account'],
            'user_actions':        ['action_id'],
            'account_buckets':     ['account_name'],
            'tax_lots':            ['lot_id'],
            'personal_notes':      ['note_id'],
            'allocation_outputs':  ['date', 'ticker', 'ledger'],
        }

        try:
            if method == 'GET':
                params = urllib.parse.parse_qs(query)
                try:
                    raw_limit = int(params.get('limit', ['100'])[0])
                except (ValueError, TypeError):
                    self._json_error(400, 'limit must be an integer')
                    return
                # [3] Clamp: no negatives, no zero, max 1000
                limit = max(1, min(raw_limit, 1000))
                with _db.local(readonly=True) as conn:
                    rows = conn.execute(
                        f"SELECT * FROM {table} LIMIT ?", (limit,)
                    ).fetchall()
                self._json_ok({'table': table, 'count': len(rows), 'rows': [dict(r) for r in rows]}, local=True)

            elif method == 'POST':
                if not body:
                    self._json_error(400, 'body required for POST')
                    return
                try:
                    payload = json.loads(body.decode('utf-8'))
                except json.JSONDecodeError as e:
                    self._json_error(400, f'invalid JSON: {e}')
                    return
                if not isinstance(payload, dict):
                    self._json_error(400, 'body must be JSON object')
                    return

                # [4] Column allowlist validation
                allowed_cols = LOCAL_TABLE_COLUMN_ALLOWLIST.get(table, set())
                unknown_fields = set(payload.keys()) - allowed_cols
                if unknown_fields:
                    self._json_error(400,
                        f'Unknown fields for {table}: {sorted(unknown_fields)}. '
                        f'Allowed: {sorted(allowed_cols)}'
                    )
                    return

                # Auto-generate IDs if missing
                if table == 'user_actions' and not payload.get('action_id'):
                    payload['action_id'] = str(uuid.uuid4())
                if table == 'tax_lots' and not payload.get('lot_id'):
                    payload['lot_id'] = str(uuid.uuid4())
                if table == 'personal_notes' and not payload.get('note_id'):
                    payload['note_id'] = str(uuid.uuid4())

                with _db.local() as conn:
                    _db.upsert(conn, table, payload, CONFLICT_COLS[table])
                self._json_ok({'status': 'ok', 'table': table, 'row': payload}, local=True)

            elif method == 'DELETE':
                params = urllib.parse.parse_qs(query)
                if not params:
                    self._json_error(400, 'DELETE requires PK params')
                    return
                pk = CONFLICT_COLS[table]
                if not all(k in params for k in pk):
                    self._json_error(400, f'DELETE requires all PK params: {pk}')
                    return
                where = ' AND '.join(f'{c} = ?' for c in pk)
                args = [params[c][0] for c in pk]
                with _db.local() as conn:
                    cur = conn.execute(f"DELETE FROM {table} WHERE {where}", args)
                    deleted = cur.rowcount
                self._json_ok({'status': 'ok', 'deleted': deleted}, local=True)

            else:
                self.send_response(405)
                self._cors_local()
                self.end_headers()

        except Exception as e:
            self._json_error(500, f'local handler error: {e}')

    # ── Response helpers ─────────────────────────────────────────────────────
    def _json_ok(self, payload, local: bool = False):
        body = json.dumps(payload, default=str).encode()
        self.send_response(200)
        if local:
            self._cors_local()
        else:
            self._cors_public()
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(body)

    # ── Alpaca live-quote proxy (keys stay server-side) ─────────────────────
    def _price_unavailable(self):
        """503 sentinel so the browser can fall back to its own quote source."""
        self.send_response(503)
        self._cors_public()
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({
            'error': 'no_live_price_provider',
            'detail': 'set ALPACA_KEY/ALPACA_SECRET (env or ~/.wealth-dashboard/secrets.env)',
        }).encode())

    def _send_json(self, obj, code=200):
        self.send_response(code)
        self._cors_public()
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(obj).encode())

    def _proxy_price_snapshot(self, query):
        params = urllib.parse.parse_qs(query)
        raw = (params.get('symbols', [''])[0] or '').strip()
        feed = (params.get('feed', ['iex'])[0] or 'iex').strip().lower()
        if feed not in ('iex', 'sip'):
            feed = 'iex'
        symbols = [s.strip().upper() for s in raw.split(',') if s.strip()]
        if not symbols:
            self._json_error(400, 'symbols required (comma-separated)')
            return

        key, secret = _alpaca_creds()
        if not (key and secret):
            self._price_unavailable()
            return

        lq = _get_live_quotes()
        symbols = symbols[:lq.MAX_SYMBOLS]
        cache_key = ('snap', feed, ','.join(sorted(symbols)))
        now = time.time()

        # light prune so the cache can't grow unbounded across many watchlists
        if len(_price_cache) > 256:
            for k, (ts, _v) in list(_price_cache.items()):
                if now - ts > max(PRICE_CACHE_TTL_SEC, CLOCK_CACHE_TTL_SEC):
                    _price_cache.pop(k, None)

        hit = _price_cache.get(cache_key)
        if hit and now - hit[0] < PRICE_CACHE_TTL_SEC:
            quotes, cached = hit[1], True
        else:
            try:
                quotes = lq.fetch_snapshots(symbols, key, secret, feed=feed)
            except urllib.error.HTTPError as e:
                self._json_error(e.code, f'Alpaca error: {e.code}')
                return
            except Exception as e:                                  # noqa: BLE001
                self._json_error(502, f'Alpaca snapshot failed: {e}')
                return
            _price_cache[cache_key] = (now, quotes)
            cached = False

        self._send_json({'quotes': quotes, 'feed': feed, 'count': len(quotes),
                         'cached': cached, 'asof': int(now)})

    def _proxy_price_clock(self):
        key, secret = _alpaca_creds()
        if not (key and secret):
            self._price_unavailable()
            return
        now = time.time()
        hit = _price_cache.get(('clock',))
        if hit and now - hit[0] < CLOCK_CACHE_TTL_SEC:
            self._send_json(hit[1])
            return
        lq = _get_live_quotes()
        try:
            clock = lq.fetch_clock(key, secret)
        except Exception as e:                                      # noqa: BLE001
            self._json_error(502, f'Alpaca clock failed: {e}')
            return
        _price_cache[('clock',)] = (now, clock)
        self._send_json(clock)

    def _proxy_fundamentals(self, query):
        """On-demand SEC fundamentals for ANY ticker (no API key needed).
        Computes ROIC/FCF/margins/P-multiples from SEC EDGAR companyfacts."""
        params = urllib.parse.parse_qs(query)
        ticker = (params.get('ticker', [''])[0] or '').strip().upper()
        if not ticker or len(ticker) > 8 or not ticker.replace('.', '').replace('-', '').isalnum():
            self._json_error(400, 'valid ticker required')
            return
        try:
            price = float(params.get('price', [''])[0])
        except (TypeError, ValueError):
            price = None

        # If the browser did not pass a quote, enrich with server-side Alpaca.
        # This lets arbitrary ticker lookups show PER/PBR/PFCF without exposing
        # ALPACA_KEY to the browser.
        if price is None:
            key, secret = _alpaca_creds()
            if key and secret:
                try:
                    quotes = _get_live_quotes().fetch_snapshots([ticker], key, secret, feed='iex')
                    q = quotes.get(ticker) or {}
                    if q.get('price'):
                        price = float(q['price'])
                except Exception:
                    price = None

        now = time.time()
        hit = _fund_cache.get(ticker)
        if hit and now - hit[0] < FUND_CACHE_TTL_SEC and price is None:
            self._send_json(hit[1])
            return
        try:
            sf = _get_sec_fundamentals()
            item = sf.compute_for_ticker(ticker, price=price)
        except Exception as e:                                      # noqa: BLE001
            self._json_error(502, f'fundamentals failed: {e}')
            return
        if price is None and item.get('available'):
            _fund_cache[ticker] = (now, item)
            if len(_fund_cache) > 512:
                for k, (ts, _v) in list(_fund_cache.items()):
                    if now - ts > FUND_CACHE_TTL_SEC:
                        _fund_cache.pop(k, None)
        self._send_json(item)

    def _json_error(self, code, msg, local: bool = False):
        body = json.dumps({'error': msg}).encode()
        self.send_response(code)
        if local:
            self._cors_local()
        else:
            self._cors_public()
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(body)

    def _cors_public(self):
        """Public CORS: allow any origin (read-only market data)."""
        self.send_header('Access-Control-Allow-Origin',  '*')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, x-api-key, anthropic-version')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS')

    def _cors_local(self):
        """Private CORS: only localhost origins (personal data / write endpoints)."""
        origin = self.headers.get('Origin', '').strip()
        if origin in _ALLOWED_LOCAL_ORIGINS:
            allowed_origin = origin
        else:
            # No origin (curl/script) or unlisted — reflect localhost default
            allowed_origin = 'http://localhost:5500'
        self.send_header('Access-Control-Allow-Origin',  allowed_origin)
        self.send_header('Access-Control-Allow-Headers',
                         'Content-Type, X-Local-Token')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS')
        self.send_header('Vary', 'Origin')

    def log_message(self, fmt, *args):
        print(f"  {self.address_string()}  {fmt % args}")


if __name__ == '__main__':
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    # Pre-generate token on startup so it's ready before first request
    token = _get_local_token()
    print(f"🔑  Local token: {token[:8]}… (stored in ~/.wealth-dashboard/local_token)")
    with http.server.HTTPServer(('', PORT), Handler) as httpd:
        print(f"✅  http://localhost:{PORT} 에서 실행 중")
        print(f"   /api/claude     → Anthropic API 프록시")
        print(f"   /api/fred       → FRED 프록시 (CORS 우회)")
        print(f"   /api/finnhub    → Finnhub 프록시 (키는 서버에만)")
        print(f"   /api/yhfin      → Yahoo Finance 캔들 프록시")
        print(f"   /api/edgar      → SEC EDGAR 프록시")
        print(f"   /api/feargreed  → CNN Fear & Greed")
        print(f"   /api/news       → RSS news aggregator")
        print(f"   /api/price/snapshot → Alpaca 실시간 시세 배치 (키는 서버에만)")
        print(f"   /api/price/clock    → Alpaca 장 개장/마감 상태")
        print(f"   /api/fundamentals   → 임의 종목 SEC 펀더멘털 온디맨드 (무키)")
        print(f"   /api/db/health  → DB 상태 (cloud + local)")
        print(f"   /api/db/query   → cloud DB 조회 (allowlist, read-only, public)")
        print(f"   /api/local/*    → local personal DB (localhost + Origin 검사)")
        print(f"   /api/snapshot   → 수동 snapshot trigger (localhost, 1/hr)")
        print(f"   종료: Ctrl+C\n")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n서버 종료")
