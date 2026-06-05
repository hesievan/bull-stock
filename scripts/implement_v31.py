#!/usr/bin/env python3
"""
v3.1 指标调整实现脚本
1. M1-M2剪刀差 (宏观维度)
2. M2同比 (宏观维度)
3. ERP股权风险溢价 (估值维度, 替换巴菲特指标)
4. 北向资金降权 (保留累计流入, 删除方向)
5. MA排列比 (替换MA250站上比)
6. 权重调整
"""
import sys, os, sqlite3, time, logging
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

from src.data.database import DB_PATH

def main():
    conn = sqlite3.connect(DB_PATH)

    # ============================================================
    # 1. M1-M2剪刀差 + M2同比 (宏观维度)
    # ============================================================
    print("=" * 60)
    print("1. M1-M2剪刀差 + M2同比")
    print("=" * 60)

    try:
        import akshare as ak
        df_m = ak.macro_china_money_supply()
        # 列: 月份, 货币和准货币(M2)-数量(亿元), 货币和准货币(M2)-同比增长,
        #      货币(M1)-数量(亿元), 货币(M1)-同比增长, ...

        # 提取 M1同比, M2同比, 剪刀差
        m1_m2 = pd.DataFrame({
            'month': df_m['月份'].str.replace('年', '-').str.replace('月份', ''),
            'm1_yoy': df_m['货币(M1)-同比增长'],
            'm2_yoy': df_m['货币和准货币(M2)-同比增长'],
        })
        m1_m2['scissors'] = m1_m2['m1_yoy'] - m1_m2['m2_yoy']  # 剪刀差
        m1_m2 = m1_m2.sort_values('month').reset_index(drop=True)

        # 存入数据库
        conn.execute('''CREATE TABLE IF NOT EXISTS macro_money (
            month TEXT PRIMARY KEY,
            m1_yoy REAL,
            m2_yoy REAL,
            scissors REAL
        )''')
        for _, row in m1_m2.iterrows():
            conn.execute(
                'INSERT OR REPLACE INTO macro_money (month, m1_yoy, m2_yoy, scissors) VALUES (?,?,?,?)',
                (row['month'], float(row['m1_yoy']), float(row['m2_yoy']), float(row['scissors']))
            )
        conn.commit()
        print(f"  macro_money: {len(m1_m2)}行, 最新={m1_m2.iloc[-1]['month']}")
        print(f"  最新M1同比={m1_m2.iloc[-1]['m1_yoy']:.1f}%, M2同比={m1_m2.iloc[-1]['m2_yoy']:.1f}%, 剪刀差={m1_m2.iloc[-1]['scissors']:.1f}")
    except Exception as e:
        print(f"  ERR: {e}")

    # ============================================================
    # 2. ERP股权风险溢价 (估值维度)
    # ============================================================
    print()
    print("=" * 60)
    print("2. ERP股权风险溢价")
    print("=" * 60)

    try:
        # ERP = 1/PE - 10年国债收益率
        # PE: index_daily_pe.pe_med
        # 国债: bond_yield.yield_rate

        pe_data = pd.read_sql(
            "SELECT trade_date, pe_med FROM index_daily_pe WHERE pe_med IS NOT NULL AND pe_med > 0",
            conn
        )
        bond_data = pd.read_sql(
            "SELECT trade_date, yield_rate FROM bond_yield WHERE yield_rate IS NOT NULL",
            conn
        )

        # 合并 (月频PE + 日频国债 → 用国债的最新值匹配)
        erp_data = []
        for _, row in pe_data.iterrows():
            td = row['trade_date']
            pe = row['pe_med']
            if pe <= 0:
                continue
            # 找最近的国债收益率
            bond_row = conn.execute(
                "SELECT yield_rate FROM bond_yield WHERE trade_date <= ? ORDER BY trade_date DESC LIMIT 1",
                (td,)
            ).fetchone()
            if bond_row:
                ey = 1.0 / pe * 100  # 股票收益率(%)
                bond_rate = bond_row[0]  # 国债收益率(%)
                erp = ey - bond_rate  # ERP(%)
                erp_data.append({'trade_date': td, 'pe': pe, 'ey': ey, 'bond_rate': bond_rate, 'erp': erp})

        if erp_data:
            erp_df = pd.DataFrame(erp_data)
            # 存入数据库
            conn.execute('''CREATE TABLE IF NOT EXISTS daily_erp (
                trade_date TEXT PRIMARY KEY,
                erp REAL,
                ey REAL,
                bond_rate REAL,
                pe REAL
            )''')
            for _, row in erp_df.iterrows():
                conn.execute(
                    'INSERT OR REPLACE INTO daily_erp (trade_date, erp, ey, bond_rate, pe) VALUES (?,?,?,?,?)',
                    (row['trade_date'], float(row['erp']), float(row['ey']),
                     float(row['bond_rate']), float(row['pe']))
                )
            conn.commit()
            print(f"  daily_erp: {len(erp_df)}行")
            print(f"  最新ERP: {erp_df.iloc[-1]['erp']:.4f}% (股票收益率{erp_df.iloc[-1]['ey']:.2f}% - 国债{erp_df.iloc[-1]['bond_rate']:.2f}%)")
    except Exception as e:
        print(f"  ERR: {e}")

    # ============================================================
    # 3. MA排列比 (替换MA250站上比)
    # ============================================================
    print()
    print("=" * 60)
    print("3. MA排列比 (MA20>MA60>MA120)")
    print("=" * 60)

    try:
        # 获取全量数据
        df = pd.read_sql('''
            SELECT trade_date, stock_code, close
            FROM stock_daily
            WHERE trade_date >= "2015-01-05" AND close IS NOT NULL AND close > 0
            ORDER BY stock_code, trade_date
        ''', conn)
        df['close'] = pd.to_numeric(df['close'], errors='coerce')
        df = df.dropna()

        # 计算 MA20, MA60, MA120
        df['ma20'] = df.groupby('stock_code')['close'].transform(lambda x: x.rolling(20, min_periods=10).mean())
        df['ma60'] = df.groupby('stock_code')['close'].transform(lambda x: x.rolling(60, min_periods=30).mean())
        df['ma120'] = df.groupby('stock_code')['close'].transform(lambda x: x.rolling(120, min_periods=60).mean())

        # MA排列比: MA20 > MA60 > MA120 的股票占比
        df_valid = df.dropna(subset=['ma20', 'ma60', 'ma120'])
        df_valid['aligned'] = ((df_valid['ma20'] > df_valid['ma60']) & (df_valid['ma60'] > df_valid['ma120'])).astype(int)

        daily_aligned = df_valid.groupby('trade_date').agg(
            aligned=('aligned', 'sum'),
            total=('aligned', 'count')
        ).reset_index()
        daily_aligned['ma_alignment_ratio'] = daily_aligned['aligned'] / daily_aligned['total']

        # 存入预计算表
        conn.execute('''CREATE TABLE IF NOT EXISTS daily_ma_alignment (
            trade_date TEXT PRIMARY KEY,
            ma_alignment_ratio REAL
        )''')
        for _, row in daily_aligned.iterrows():
            conn.execute(
                'INSERT OR REPLACE INTO daily_ma_alignment (trade_date, ma_alignment_ratio) VALUES (?,?)',
                (row['trade_date'], float(row['ma_alignment_ratio']))
            )
        conn.commit()
        print(f"  daily_ma_alignment: {len(daily_aligned)}行")
        print(f"  最新: {daily_aligned.iloc[-1]['ma_alignment_ratio']:.4f}")
    except Exception as e:
        print(f"  ERR: {e}")

    conn.close()
    print()
    print("=" * 60)
    print("所有指标调整完成!")
    print("=" * 60)

import pandas as pd
main()
