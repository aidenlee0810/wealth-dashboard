# Wealth Dashboard — Research Platform

> Wall Street-grade Quant + Fundamental Research Platform  
> Phase 1.1 · SQLite DB · Local/Static Mode · Privacy-first

---

## 목차

1. [아키텍처 개요](#아키텍처-개요)
2. [빠른 시작 (Local Mode)](#빠른-시작-local-mode)
3. [DB 초기화](#db-초기화)
4. [실행 모드 설명](#실행-모드-설명)
5. [보안 원칙](#보안-원칙)
6. [GitHub Secrets 설정](#github-secrets-설정)
7. [Pre-commit Hook 설치](#pre-commit-hook-설치)
8. [테스트 실행](#테스트-실행)
9. [API 엔드포인트 레퍼런스](#api-엔드포인트-레퍼런스)
10. [Phase 2 전 체크리스트](#phase-2-전-체크리스트)

---

## 아키텍처 개요

```
┌─── CLOUD (git repo, GitHub Pages, GitHub Actions) ──────────────────┐
│  data/db/research_market.sqlite  ←  daily snapshot (GHA cron)       │
│  data/views/*.json               ←  static snapshots for GitHub Pages│
│  ✗ 개인 포트폴리오/계좌/거래 데이터 절대 없음                         │
└──────────────────────────────────────────────────────────────────────┘
                          ↕ git pull
┌─── LOCAL (python server.py 실행 시) ────────────────────────────────┐
│  ~/.wealth-dashboard/personal.sqlite  ←  개인 포트폴리오 전용         │
│  /api/local/*  ←  localhost + Origin 검사 + X-Local-Token 보호       │
└──────────────────────────────────────────────────────────────────────┘
```

**데이터 분리 6-layer 방어:**
1. `.gitignore` — personal.sqlite, logs/, *.jsonl 제외
2. `scripts/pre-commit-personal-data-check.sh` — staged 파일 차단
3. GitHub Actions pre-check — personal data 참조 시 abort
4. `db.py:PERSONAL_TABLES` — 상수 정의
5. `/api/db/query` whitelist — personal 테이블 항상 403
6. `tests/test_data_separation.py` — CI 자동 검증

---

## 빠른 시작 (Local Mode)

### 사전 요건

- Python 3.9+
- 추가 패키지 없음 (stdlib only)

### 1단계: DB 초기화

```bash
cd /Users/aidenlee/wealth-dashboard
python db.py init
```

성공 시 출력:
```
{"applied_migrations": {"cloud": 1, "local": 1}, "health": {...}}
```

두 DB가 생성됩니다:
- `data/db/research_market.sqlite` — cloud DB (git에 포함)
- `~/.wealth-dashboard/personal.sqlite` — 개인 DB (절대 git에 포함 안 됨)

### 2단계: Pre-commit Hook 설치

```bash
bash scripts/install-hooks.sh
```

한 번만 실행. 이후 `git commit`마다 개인 데이터 파일을 자동 차단합니다.

### 3단계: 서버 실행

```bash
python server.py
```

출력:
```
🔑  Local token: a3f8b2c1… (stored in ~/.wealth-dashboard/local_token)
✅  http://localhost:5500 에서 실행 중
```

### 4단계: 브라우저에서 열기

```
http://localhost:5500/research.html
```

헤더 오른쪽에 DB 모드 배지가 표시됩니다:
- 🟢 `DB live` — Local Mode 정상
- 🟡 `Static snapshot` — Static Mode (GitHub Pages)
- 🔴 `Fallback` — 데이터 없음

### 5단계: DB 상태 확인

```bash
curl http://localhost:5500/api/db/health | python3 -m json.tool
```

---

## DB 초기화

```bash
# 초기화 (cloud + local 동시)
python db.py init

# 상태만 확인 (write 없음)
python db.py health

# Cloud DB에 개인 테이블 유출 여부 검사
python db.py verify
```

### DB 경로

| DB | 경로 | git 포함 |
|----|------|---------|
| Cloud | `data/db/research_market.sqlite` | ✅ 포함 |
| Local (Personal) | `~/.wealth-dashboard/personal.sqlite` | ❌ 절대 포함 안 됨 |

---

## Dynamic Universe (Phase 2)

6개 레이어를 머지해 **555개 종목**의 동적 유니버스를 구성합니다 (고정 67개 → 동적).

```bash
# 1) 인덱스 구성종목 실데이터 갱신 (Wikipedia 스크레이프, stdlib only)
python jobs/scrape_indices.py            # data/sp500.json + data/nasdaq100.json

# 2) 유니버스 빌드 (ticker_master + universe_membership 갱신, persistence 규칙 적용)
python jobs/universe_builder.py --summary
python jobs/universe_builder.py --dry-run   # DB 쓰기 없이 미리보기
```

레이어:

| 레이어 | 소스 | 파일 |
|--------|------|------|
| A. Core 워치리스트 | 사용자 큐레이션 (49) | `data/core_watchlist.json` + localStorage |
| B. 인덱스 | S&P 500 (503) + 나스닥100 (101) | `data/sp500.json`, `data/nasdaq100.json` |
| C. 섹터 상위보유 | 11 SPDR × 10 | `data/sector_holdings.json` |
| D. 테마 바스켓 | 15 테마 | `data/theme_baskets.json` |
| E. 모멘텀 발굴 | 동적 (가격/RVOL) | Phase 3 |
| F. 이벤트/뉴스 | RSS, 13F | Phase 3 |

- **Persistence**: `days_active>=5`만 Core 승격. 1일 급등은 Tactical만 가능.
- **Provenance**: 종목마다 `source_buckets`로 어느 레이어 출신인지 추적 (UI 칩으로 표시).
- **Data Contracts (§23)**: 모든 외부 API 응답은 `jobs/contracts/`에서 스키마 검증 (drift 자동 감지).
- 브라우저(`research/universe-builder.js`)와 Python(`jobs/universe_builder.py`)이 동일 결과 생성.

---

## 실행 모드 설명

| 모드 | 조건 | 기능 |
|------|------|------|
| **Local** | `python server.py` 실행 중 | 전체 기능. /api/db/query + /api/local/* 사용 가능 |
| **Static** | GitHub Pages 배포, server.py 없음 | data/views/*.json 읽기. 개인 데이터 없음 |
| **Fallback** | 위 둘 다 실패 | localStorage만 사용. 기능 제한 |

### Static Mode (GitHub Pages)

`data/views/latest_*.json` 파일이 있으면 GitHub Pages에서 `research.html`이 Static Mode로 작동합니다. Python 없이도 시장 데이터를 볼 수 있습니다.

**현재 static views (스켈레톤 상태, Phase 3 이후 실제 데이터로 채워짐):**
```
data/views/
  latest_snapshot_health.json
  latest_market_regime.json
  latest_universe_summary.json
  latest_candidates.json
  latest_candidate_core.json
  latest_candidate_watchlist.json
  latest_candidate_tactical.json
  latest_sector_theme.json
  latest_validation_summary.json
  latest_backtest_summary.json
  latest_failed_jobs.json
  latest_alerts.json
```

---

## 보안 원칙

### 개인 데이터가 Cloud DB에 들어가면 안 되는 이유

`research_market.sqlite`는 GitHub에 공개 커밋됩니다. 여기에 포트폴리오, 계좌, 거래 기록이 들어가면:
- 자산 규모가 공개됨
- 거래 패턴이 노출됨
- 세금 정보가 유출될 수 있음

**개인 데이터는 반드시 `~/.wealth-dashboard/personal.sqlite`에만 저장해야 합니다.**

### /api/local/* 보안 (Phase 1.1 강화)

| 방어 레이어 | 내용 |
|------------|------|
| IP 검사 | 127.0.0.1 / ::1 / localhost만 허용 |
| Origin 검사 | 외부 Origin이면 403 (CSRF 방지) |
| X-Local-Token | 서버 시작 시 생성, 브라우저에서 자동 전송 |
| Column allowlist | POST payload의 알 수 없는 필드는 400 거부 |
| limit clamp | `max(1, min(requested, 1000))` — 음수 불가 |

### /api/db/query 보안

- 28개 허용 테이블만 조회 가능 (allowlist)
- 컬럼별 허용 목록 강제
- order_by 허용 컬럼만 사용 가능
- limit 최대 500
- SQL injection: 모든 파라미터 parameterized query
- raw SQL 입력 절대 불가
- personal 테이블 요청 시 항상 403
- read-only connection (`PRAGMA query_only = ON`)

### API 키 관리

```
❌ 절대 하지 말 것:
  - 코드에 API 키 하드코딩
  - config.js에 실제 키 커밋
  - .env 파일 커밋

✅ 올바른 방법:
  - 로컬: research.html > ⚙️ API 설정 UI에서 입력 (localStorage)
  - GitHub Actions: Repository Secrets 사용 (FINNHUB_KEY, FRED_KEY)
```

---

## GitHub Secrets 설정

GitHub Actions daily snapshot이 작동하려면:

1. GitHub 저장소 → **Settings** → **Secrets and variables** → **Actions**
2. 다음 secrets 추가:

| Secret 이름 | 용도 |
|------------|------|
| `ALPACA_KEY` | Alpaca API key — 권장 실가격 history provider |
| `ALPACA_SECRET` | Alpaca API secret — `ALPACA_KEY`와 함께 필요 |
| `TIINGO_KEY` | Tiingo API key — 선택 실가격 history fallback |
| `FINNHUB_KEY` | Finnhub API key — 주가 + `/stock/metric` 펀더멘털 비율 |
| `FRED_KEY` | FRED API key — 매크로 경제 데이터(`macro_daily`) |

추가로 **Variables** 탭(비밀 아님)에 SEC 공정접근(fair-access)용 연락처를 권장:

| Variable 이름 | 용도 |
|------------|------|
| `SEC_USER_AGENT` | `"이름 your@email"` — SEC EDGAR 요청 User-Agent (SEC는 실제 연락처를 요구) |

### 가격·펀더멘털 자동 동기화 (매 스냅샷)

`jobs/fetch.py`는 **키를 `config.js`가 아니라 환경변수**(`os.environ`)에서 읽습니다.
GHA workflow(`daily-snapshot.yml`)가 위 secrets를 env로 주입하면, 매일 cron 실행 시
`jobs/daily_snapshot.py`가:

1. **step 2** — `prices.update_prices()`로 Alpaca → Tiingo → Yahoo 순서의 실가격 history를 시도.
   provider별 실패 이유는 `latest_snapshot_health.data_realism.price_provider_failures`에 기록.
2. **step 8** — `financials.build_financials()`로 SEC EDGAR CompanyFacts(키 불필요) +
   Finnhub metrics를 가져와 `cleaned_financials`/`normalized_financials`에 upsert.
3. **step 9** — fundamentals가 채워진 뒤 features를 deep re-score → `bq/val/growth`에 반영.

밸류에이션(`val_score`, PER/PFCF 기반 점수)은 ticker의 전체 가격 history가
`real`일 때만 켜집니다. synthetic 또는 mixed history는 `price_quality`가 차단하고,
mixed history는 technical DQ도 낮춥니다.

키 값은 **절대 출력/커밋되지 않습니다.** 라이브 실행은 키 **존재 여부(boolean)** 만
로그(`api_key_presence`)에 남깁니다. 가격 provider 키가 없어도 SEC 기반 펀더멘털은
동작하지만, 실가격 history가 없으면 `valuation_populated_count=0`이 정상입니다
(`FRED_KEY` 없으면 `macro_daily`만 비어짐).

> **주의**: API 키를 코드나 workflow YAML에 직접 쓰지 마세요.  
> GitHub Secrets는 로그에도 마스킹되어 표시됩니다. 키가 이미 `config.js` 등 git
> 히스토리에 노출됐다면 **rotate(재발급) 후 환경변수/secrets로 이전**하세요.

---

## Pre-commit Hook 설치

```bash
bash scripts/install-hooks.sh
```

### 동작 방식

`scripts/pre-commit-personal-data-check.sh`가 매 `git commit`마다 실행됩니다:

**차단하는 것:**
- `personal.sqlite*` 파일
- `user_actions*.csv/json/sqlite`
- `portfolio_snapshots*.*`
- `account_buckets*.*`, `tax_lots*.*`
- `data/personal/` 폴더
- `local_token` 파일

**경고하는 것 (차단하지 않음):**
- 코드 파일에서 API 키 패턴 감지 시 경고 출력

### 수동 테스트

```bash
# 차단이 작동하는지 확인
echo "test" > personal.sqlite
git add personal.sqlite
git commit -m "test"
# → "Pre-commit hook FAILED" 출력, commit 차단
git reset HEAD personal.sqlite && rm personal.sqlite
```

### hook 없는 환경에서 수동 실행

```bash
bash scripts/pre-commit-personal-data-check.sh
```

---

## 테스트 실행

### Unit Tests (서버 없이 실행 가능)

```bash
python db.py init
python -m pytest tests/test_data_separation.py -v
```

### Integration Tests (서버 필요)

```bash
python server.py &
sleep 1
python -m pytest tests/test_server_endpoints.py -v
```

### 예상 결과 (DB 초기화 후, 서버 없을 때)

```
tests/test_data_separation.py
  PASS  test_1_cloud_db_no_personal_tables
  PASS  test_2_git_status_no_personal_files
  SKIP  test_3_gha_workflows_no_personal_refs  [No GHA workflows yet]
  SKIP  test_4_jobs_no_personal_db_refs        [expected — jobs/ dir empty]
  SKIP  test_5_api_db_query_rejects_personal   [Server not running — expected]
  PASS  test_6_validate_and_query_rejects_personal_unit
  PASS  test_7_sql_injection_patterns_rejected

tests/test_server_endpoints.py
  SKIP  * (all)  [Server not running — expected in CI]

Summary: 4 PASS · 5 SKIP · 0 FAIL
```

> 서버 없는 환경의 skip은 **정상**입니다.  
> `0 FAIL`이 핵심 기준입니다.

---

## API 엔드포인트 레퍼런스

### Public Endpoints (CORS: `*`)

| Method | URL | 설명 |
|--------|-----|------|
| GET | `/api/db/health` | DB 상태. localhost에서만 `local_token` 포함 |
| GET | `/api/db/query?table=&cols=&...` | Cloud DB 조회 (whitelist, read-only) |
| GET | `/api/fred?series_id=&api_key=` | FRED 프록시 |
| GET | `/api/yhfin?symbol=&period=` | Yahoo Finance 캔들 프록시 |
| GET | `/api/edgar/...` | SEC EDGAR 프록시 |
| GET | `/api/feargreed` | CNN F&G 프록시 |
| GET | `/api/news` | RSS 뉴스 집계 |
| POST | `/api/claude` | Anthropic API 프록시 |

### Private Endpoints (localhost + Origin 검사, CORS: `http://localhost:5500`)

| Method | URL | 설명 |
|--------|-----|------|
| GET/POST | `/api/local/portfolio` | 포트폴리오 스냅샷 |
| GET/POST/DELETE | `/api/local/actions` | 사용자 행동 기록 |
| GET/POST | `/api/local/targets` | 목표 비중 |
| GET/POST/DELETE | `/api/local/lots` | 세금 lot |
| GET/POST/DELETE | `/api/local/notes` | 메모 |
| GET/POST | `/api/local/buckets` | 계좌 버킷 |
| GET/POST | `/api/local/allocations` | 배분 출력 |
| POST | `/api/snapshot` | 수동 snapshot 트리거 (1/hr) |

**`/api/db/query` 파라미터:**
- `table`: 허용된 테이블 이름 (28개 allowlist)
- `cols`: 콤마 구분 컬럼 목록 (생략 시 전체)
- `order_by`: `"컬럼 ASC"` 또는 `"컬럼 DESC"`
- `limit`: 1-500 (기본 100)
- where 필터: `ticker=NVDA` 또는 `date__gte=2026-01-01`

---

## Phase 2 전 체크리스트

### DB / 인프라
- [ ] `python db.py init` 성공 (cloud + local 두 DB 생성)
- [ ] `python db.py verify` 성공 (개인 테이블 누출 없음)
- [ ] `python db.py health` JSON 응답 정상

### 보안
- [ ] `bash scripts/install-hooks.sh` 실행 완료
- [ ] personal.sqlite staged → commit 차단 확인
- [ ] 외부 Origin `/api/local/*` 요청 → 403 확인
- [ ] `/api/db/query?table=user_actions` → 403 확인
- [ ] `limit=-1` 요청 → 1로 clamp 또는 400

### UI
- [ ] `http://localhost:5500/research.html` 정상 로드
- [ ] 헤더에 🟢 `DB live` 배지 표시
- [ ] 11개 탭 전부 기존과 동일하게 작동 (regression 없음)

### Tests
- [ ] `python -m pytest tests/ -v` → 0 FAIL
- [ ] Unit tests: test_1, test_2, test_6, test_7 PASS
- [ ] Server tests: expected SKIP (서버 없을 때)

### Static Mode
- [ ] `data/views/` 아래 12개 JSON 파일 존재 확인

---

## 관련 문서

- [`docs/runbook.md`](docs/runbook.md) — 장애 대응 8개 시나리오
- [`docs/adr/001-sqlite-vs-duckdb.md`](docs/adr/001-sqlite-vs-duckdb.md) — SQLite 선택 근거
- [`docs/adr/002-cloud-local-data-separation.md`](docs/adr/002-cloud-local-data-separation.md) — 데이터 분리 설계

## Phase 로드맵

| Phase | 내용 | 상태 |
|-------|------|------|
| 1 | DB 인프라, server.py, db-client.js, migrations, static views | ✅ 완료 |
| 1.1 | 보안 패치 (CORS, CSRF, allowlist, badge, scripts) | ✅ 완료 |
| 2 | Dynamic Universe v1 (6-layer, 555 ticker) + Data Contracts (§23) + 4-layer pipeline (§24) | ✅ 완료 |
| 3 | Daily Snapshot + GitHub Actions cron + Reconciliation (§25) | 🔜 다음 |
| 4 | Feature Store + model-type별 fundamental scoring | 📅 계획됨 |
| 5 | Risk Governor (15 gates) | 📅 계획됨 |
| 6 | Validation v2 (EV, PF, MAE/MFE, 3-ledger) | 📅 계획됨 |
| 7 | Python Backtest Engine (walk-forward, survivorship bias) | 📅 계획됨 |
| 8 | Personal Portfolio Local Layer (3-ledger 비교) | 📅 계획됨 |
