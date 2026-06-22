#!/usr/bin/env python3
"""
A股牛市热度指数 — 主入口
每日收盘后运行：python scripts/run.py
"""
import sys
import os
import logging
from datetime import date

# 项目根目录加入路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.config import load_config
from src.data.fetcher import update_all_data
from src.indicators.calculator import calculate_all_market_heat, calculate_sector_heat
from src.output.html_generator import generate_html_page

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("data/run.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


def main():
    logger.info("=" * 60)
    logger.info("A股牛市热度指数 — 开始计算")
    logger.info("=" * 60)

    cfg = load_config()
    today = date.today().strftime("%Y-%m-%d")
    logger.info(f"日期: {today}")

    # Step 1: 更新数据
    logger.info("Step 1: 更新本地数据...")
    try:
        update_all_data(cfg)
        logger.info("  ✓ 数据更新完成")
    except Exception as e:
        logger.error(f"  ✗ 数据更新失败: {e}", exc_info=True)
        sys.exit(1)

    # Step 2: 计算全市场热度
    logger.info("Step 2: 计算全市场热度指数...")
    try:
        all_market_result = calculate_all_market_heat(cfg)
        if all_market_result:
            logger.info(
                f"  ✓ 综合热度: {all_market_result['composite']:.1f} "
                f"({all_market_result['level_label']})"
            )
        else:
            logger.warning("  ⚠ 无法计算全市场热度（数据不足）")
    except Exception as e:
        logger.error(f"  ✗ 全市场热度计算失败: {e}", exc_info=True)
        all_market_result = None

    # Step 3: 计算板块热度
    logger.info("Step 3: 计算板块热度指数...")
    sector_results = {}
    sector_names = {
        "chinext": "创业板",
        "hs300": "沪深300",
        "zz500": "中证500",
        "zz1000": "中证1000",
        "bse": "北交所",
    }
    for key, name in sector_names.items():
        try:
            result = calculate_sector_heat(cfg, key)
            if result:
                sector_results[key] = result
                logger.info(f"  ✓ {name}: {result['composite']:.1f} ({result['level_label']})")
            else:
                logger.warning(f"  ⚠ {name}: 数据不足，跳过")
        except Exception as e:
            logger.error(f"  ✗ {name} 计算失败: {e}", exc_info=True)

    # Step 4: 生成 HTML 页面
    logger.info("Step 4: 生成 HTML 报告...")
    try:
        all_results = {
            "all_market": all_market_result,
            "sectors": sector_results,
        }
        generate_html_page(cfg, all_results, today)
        logger.info("  ✓ HTML 报告已生成")
    except Exception as e:
        logger.error(f"  ✗ HTML 生成失败: {e}", exc_info=True)

    # Step 5: 推送通知
    notify_cfg = cfg.get("notification", {})
    webhook = notify_cfg.get("feishu_webhook", "")
    if webhook and all_market_result:
        all_market_result["composite"]
        level_key = all_market_result.get("level_key", "")
        notify_on = notify_cfg.get("notify_on", ["extreme"])
        if level_key in notify_on:
            logger.info(f"Step 5: 热度处于「{all_market_result['level_label']}」区间，推送飞书通知...")
            try:
                from src.output.notifier import send_feishu_notification
                send_feishu_notification(webhook, all_market_result, sector_results, today)
                logger.info("  ✓ 飞书通知已发送")
            except Exception as e:
                logger.error(f"  ✗ 飞书通知发送失败: {e}")

    logger.info("=" * 60)
    logger.info("完成！")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
