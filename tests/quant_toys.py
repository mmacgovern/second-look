"""Small toy portfolios shared by the quant tests. Expected values are worked out by hand in each test.

Toy portfolio ($10,000 total):
    FUNDA  $4,000  (40%)   top holdings AAA 50%, BBB 30%   sectors tech 60%, health 40%   ER 0.10%
    FUNDB  $2,000  (20%)   top holdings AAA 20%, CCC 40%   sectors tech 50%, energy 50%   ER 1.00%
    AAA    $3,000  (30%)   stock, Technology
    CASH   $1,000  (10%)
"""

TOY_VALUES = {"FUNDA": 4000.0, "FUNDB": 2000.0, "AAA": 3000.0, "CASH": 1000.0}

TOY_PROFILES = {
    "FUNDA": {
        "ticker": "FUNDA", "name": "Fund A", "quote_type": "ETF", "expense_ratio": 0.001,
        "top_holdings": [{"symbol": "AAA", "name": "Aaa Corp", "weight": 0.5},
                         {"symbol": "BBB", "name": "Bbb Corp", "weight": 0.3}],
        "sector_weights": {"technology": 0.6, "healthcare": 0.4},
        "asset_classes": {"stockPosition": 1.0},
    },
    "FUNDB": {
        "ticker": "FUNDB", "name": "Fund B", "quote_type": "ETF", "expense_ratio": 0.01,
        "top_holdings": [{"symbol": "AAA", "name": "Aaa Corp", "weight": 0.2},
                         {"symbol": "CCC", "name": "Ccc Corp", "weight": 0.4}],
        "sector_weights": {"technology": 0.5, "energy": 0.5},
        "asset_classes": {"stockPosition": 1.0},
    },
    "AAA": {"ticker": "AAA", "name": "Aaa Corp", "quote_type": "EQUITY", "sector": "Technology"},
    "BBB": {"ticker": "BBB", "name": "Bbb Corp", "quote_type": "EQUITY", "sector": "Healthcare"},
}
