// Public config. Private account settings are intentionally omitted.
// The public site serves static research views; server-side provider keys live in
// GitHub Actions Secrets and are never shipped to browsers.
const CONFIG = {
  PUBLIC_SITE: true,
  PORTFOLIO_TARGETS: {},
  ACCOUNT_RULES: [],
  REBALANCE_THRESHOLD: 5,
};

const RESEARCH_CONFIG = {
  FINNHUB_KEY: '',
  FMP_KEY: '',
  FRED_KEY: '',
  GURUS: {
    '워렌 버핏 (Berkshire Hathaway)': '0001067983',
    '빌 액만 (Pershing Square)': '0001336528',
    '마이클 버리 (Scion Asset Mgmt)': '0001649339',
    '데이비드 아인혼 (Greenlight)': '0001079114',
    '데이비드 테퍼 (Appaloosa)': '0001003237',
    '스탠 드러켄밀러 (Duquesne)': '0001536411',
    '세스 클라만 (Baupost Group)': '0001061219',
    '모니쉬 파브라이 (Pabrai Funds)': '0001173334',
    '하워드 마크스 (Oaktree Capital)': '0000949509',
  },
};
