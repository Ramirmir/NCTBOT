import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import bot  # noqa: E402


class StockTests(unittest.TestCase):
    def test_parse_nested_api_payload(self) -> None:
        payload = {
            "data": {
                "normal_stock": [{"name": "Rocket"}, {"fruit": "Spin"}],
                "mirage_stock": ["Kitsune", {"display_name": "Dragon"}],
            }
        }
        self.assertEqual(
            bot.parse_stock(payload),
            bot.Stock(normal=("Rocket", "Spin"), mirage=("Kitsune", "Dragon")),
        )

    def test_message_format_and_moscow_time(self) -> None:
        stock = bot.Stock(normal=("Rocket",), mirage=("Kitsune", "Dragon"))
        result = bot.format_message(stock, datetime(2026, 1, 2, 20, 5, tzinfo=UTC))
        self.assertEqual(
            result,
            "Blox Fruits Stock обновился\n\n"
            "Normal Stock\n\n- Rocket\n\n"
            "Mirage Stock\n\n- Kitsune\n- Dragon\n\n"
            "Время обновления: 23:05 (UTC+3)",
        )

    def test_missing_mirage_is_rejected(self) -> None:
        with self.assertRaises(bot.BotError):
            bot.parse_stock({"normal_stock": ["Rocket"]})

    def test_canonical_stock_ignores_item_order(self) -> None:
        first = bot.Stock(normal=("Rocket", "Spin"), mirage=("Dragon", "Kitsune"))
        second = bot.Stock(normal=("Spin", "Rocket"), mirage=("Kitsune", "Dragon"))
        self.assertEqual(first.canonical(), second.canonical())


if __name__ == "__main__":
    unittest.main()
