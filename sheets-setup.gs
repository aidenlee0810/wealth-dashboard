/**
 * WEALTH DASHBOARD — Tiller Sheet Setup
 * ───────────────────────────────────────────────────────────────────
 * 사용법:
 *   1. Tiller Google 시트 열기
 *   2. Extensions → Apps Script
 *   3. 이 파일 전체를 붙여넣고 저장
 *   4. 상단 드롭다운에서 "setupWealthSheets" 선택 → ▶ Run
 *   5. 권한 승인 → 시트 3개 자동 생성됨
 * ───────────────────────────────────────────────────────────────────
 */

function setupWealthSheets() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const ui = SpreadsheetApp.getUi();

  setupHoldingsSheet(ss);
  setupTaxEventsSheet(ss);
  setupCreditCardsSheet(ss);

  ui.alert(
    '✅ 설정 완료',
    'Holdings, Tax Events, Credit Cards 시트가 생성됐습니다.\n\n' +
    'Holdings 시트의 Current Price 열에는 =GOOGLEFINANCE(A2) 수식이 설정되어 있습니다.\n\n' +
    '이제 config.js에 SHEET_ID를 입력하고 대시보드를 열면 됩니다.',
    ui.ButtonSet.OK
  );
}

// ── Holdings ──────────────────────────────────────────────────────

function setupHoldingsSheet(ss) {
  const SHEET_NAME = 'Holdings';
  let sheet = ss.getSheetByName(SHEET_NAME);

  if (sheet) {
    const res = SpreadsheetApp.getUi().alert(
      SHEET_NAME + ' 시트가 이미 존재합니다. 덮어쓸까요?',
      SpreadsheetApp.getUi().ButtonSet.YES_NO
    );
    if (res !== SpreadsheetApp.getUi().Button.YES) return;
    sheet.clearContents();
  } else {
    sheet = ss.insertSheet(SHEET_NAME);
  }

  // Headers
  const headers = [
    'Ticker', 'Name', 'Type', 'Shares', 'Cost Basis',
    'Purchase Date', 'Account', 'Status', 'Current Price',
    'Annual Dividend', 'Notes'
  ];
  sheet.getRange(1, 1, 1, headers.length).setValues([headers]);

  // Style header row
  const headerRange = sheet.getRange(1, 1, 1, headers.length);
  headerRange.setBackground('#1e293b').setFontColor('#94a3b8')
             .setFontWeight('bold').setFontSize(10);

  // Sample data rows
  const samples = [
    ['VTI',  'Vanguard Total Market ETF',     'ETF',   120,  195.00, '2024-03-15', 'Fidelity Brokerage', 'Open', '', 3.20, ''],
    ['VXUS', 'Vanguard Total Intl ETF',       'ETF',   80,   52.00,  '2024-04-01', 'Fidelity Brokerage', 'Open', '', 1.80, ''],
    ['BND',  'Vanguard Total Bond ETF',       'Bond',  60,   72.00,  '2024-06-01', 'Fidelity Brokerage', 'Open', '', 3.10, ''],
    ['AAPL', 'Apple Inc',                     'Stock', 15,   155.00, '2023-11-01', 'Fidelity Brokerage', 'Open', '', 0.98, ''],
    ['MSFT', 'Microsoft Corp',                'Stock', 10,   290.00, '2023-12-01', 'Fidelity Brokerage', 'Open', '', 3.00, ''],
  ];
  sheet.getRange(2, 1, samples.length, headers.length).setValues(samples);

  // Set GOOGLEFINANCE formula in Current Price column (col 9) for each sample row
  for (let r = 2; r <= samples.length + 1; r++) {
    sheet.getRange(r, 9).setFormula(`=IFERROR(GOOGLEFINANCE(A${r},"price"),"")`);
  }

  // Format columns
  sheet.setColumnWidth(1, 70);   // Ticker
  sheet.setColumnWidth(2, 220);  // Name
  sheet.setColumnWidth(3, 70);   // Type
  sheet.setColumnWidth(4, 70);   // Shares
  sheet.setColumnWidth(5, 90);   // Cost Basis
  sheet.setColumnWidth(6, 110);  // Purchase Date
  sheet.setColumnWidth(7, 160);  // Account
  sheet.setColumnWidth(8, 70);   // Status
  sheet.setColumnWidth(9, 110);  // Current Price
  sheet.setColumnWidth(10, 110); // Annual Dividend
  sheet.setColumnWidth(11, 200); // Notes

  // Number formats
  sheet.getRange(2, 4, 100, 1).setNumberFormat('#,##0.####'); // Shares
  sheet.getRange(2, 5, 100, 1).setNumberFormat('$#,##0.00');  // Cost Basis
  sheet.getRange(2, 9, 100, 1).setNumberFormat('$#,##0.00');  // Current Price
  sheet.getRange(2, 10, 100, 1).setNumberFormat('$#,##0.00'); // Dividend

  // Freeze header
  sheet.setFrozenRows(1);

  Logger.log('Holdings sheet created.');
}

// ── Tax Events ────────────────────────────────────────────────────

function setupTaxEventsSheet(ss) {
  const SHEET_NAME = 'Tax Events';
  let sheet = ss.getSheetByName(SHEET_NAME);

  if (sheet) {
    const res = SpreadsheetApp.getUi().alert(
      SHEET_NAME + ' 시트가 이미 존재합니다. 덮어쓸까요?',
      SpreadsheetApp.getUi().ButtonSet.YES_NO
    );
    if (res !== SpreadsheetApp.getUi().Button.YES) return;
    sheet.clearContents();
  } else {
    sheet = ss.insertSheet(SHEET_NAME);
  }

  const headers = [
    'Date', 'Ticker', 'Shares Sold', 'Cost Basis',
    'Sale Price', 'Gain/Loss', 'Holding Period', 'NRA Exempt', 'Notes'
  ];
  sheet.getRange(1, 1, 1, headers.length).setValues([headers]);

  const headerRange = sheet.getRange(1, 1, 1, headers.length);
  headerRange.setBackground('#1e293b').setFontColor('#94a3b8')
             .setFontWeight('bold').setFontSize(10);

  // Sample row with formula: Gain/Loss = (Sale Price - Cost Basis) × Shares Sold
  const sampleRow = [
    [new Date().toISOString().split('T')[0], 'EXAMPLE', 10, 200.00, 250.00,
     '=(E2-D2)*C2', 'Long-term', 'Yes', '매도 예시 — 이 행 삭제 후 실제 매도 기록']
  ];
  sheet.getRange(2, 1, 1, headers.length).setValues(sampleRow);
  sheet.getRange(2, 6).setFormula('=(E2-D2)*C2');

  // Columns
  sheet.setColumnWidth(1, 100);  // Date
  sheet.setColumnWidth(2, 80);   // Ticker
  sheet.setColumnWidth(3, 100);  // Shares
  sheet.setColumnWidth(4, 100);  // Cost Basis
  sheet.setColumnWidth(5, 100);  // Sale Price
  sheet.setColumnWidth(6, 100);  // Gain/Loss
  sheet.setColumnWidth(7, 110);  // Holding Period
  sheet.setColumnWidth(8, 100);  // NRA Exempt
  sheet.setColumnWidth(9, 250);  // Notes

  sheet.getRange(2, 4, 100, 3).setNumberFormat('$#,##0.00');
  sheet.getRange(2, 6, 100, 1).setNumberFormat('$#,##0.00');

  // Data validation for Holding Period
  const hpRule = SpreadsheetApp.newDataValidation()
    .requireValueInList(['Long-term', 'Short-term'], true).build();
  sheet.getRange(2, 7, 100, 1).setDataValidation(hpRule);

  // Data validation for NRA Exempt
  const nraRule = SpreadsheetApp.newDataValidation()
    .requireValueInList(['Yes', 'No'], true).build();
  sheet.getRange(2, 8, 100, 1).setDataValidation(nraRule);

  sheet.setFrozenRows(1);
  Logger.log('Tax Events sheet created.');
}

// ── Credit Cards ──────────────────────────────────────────────────

function setupCreditCardsSheet(ss) {
  const SHEET_NAME = 'Credit Cards';
  let sheet = ss.getSheetByName(SHEET_NAME);

  if (sheet) {
    const res = SpreadsheetApp.getUi().alert(
      SHEET_NAME + ' 시트가 이미 존재합니다. 덮어쓸까요?',
      SpreadsheetApp.getUi().ButtonSet.YES_NO
    );
    if (res !== SpreadsheetApp.getUi().Button.YES) return;
    sheet.clearContents();
  } else {
    sheet = ss.insertSheet(SHEET_NAME);
  }

  const headers = [
    'Card Name', 'Bank', 'Annual Fee', 'Cashback Rate',
    'Category Bonus', 'Points Balance', 'Notes'
  ];
  sheet.getRange(1, 1, 1, headers.length).setValues([headers]);

  const headerRange = sheet.getRange(1, 1, 1, headers.length);
  headerRange.setBackground('#1e293b').setFontColor('#94a3b8')
             .setFontWeight('bold').setFontSize(10);

  // Sample cards — user replaces with their actual cards
  const samples = [
    ['Chase Sapphire Reserve', 'Chase', 550, 1.5, 'Travel,Dining', 85000,
     '$300 travel credit + Priority Pass. 3x Travel & Dining'],
    ['Citi Double Cash',       'Citi',  0,   2,   '',              0,
     '2% on everything (1% buy + 1% pay)'],
    ['Amex Blue Cash Everyday','Amex',  0,   1,   'Groceries',     0,
     '3% U.S. supermarkets (up to $6k/yr), 2% gas & transit'],
  ];
  sheet.getRange(2, 1, samples.length, headers.length).setValues(samples);

  sheet.setColumnWidth(1, 200);  // Card Name
  sheet.setColumnWidth(2, 80);   // Bank
  sheet.setColumnWidth(3, 90);   // Annual Fee
  sheet.setColumnWidth(4, 110);  // Cashback Rate
  sheet.setColumnWidth(5, 160);  // Category Bonus
  sheet.setColumnWidth(6, 120);  // Points Balance
  sheet.setColumnWidth(7, 300);  // Notes

  sheet.getRange(2, 3, 100, 1).setNumberFormat('$#,##0');  // Annual Fee
  sheet.getRange(2, 4, 100, 1).setNumberFormat('0.0"%"');  // Cashback Rate
  sheet.getRange(2, 6, 100, 1).setNumberFormat('#,##0');   // Points Balance

  sheet.setFrozenRows(1);
  Logger.log('Credit Cards sheet created.');
}

// ── Quick test: log sheet names ───────────────────────────────────
function listSheets() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  ss.getSheets().forEach(s => Logger.log(s.getName()));
}
