import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

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

    def test_parse_gamersberg_payload(self) -> None:
        payload = {
            "success": True,
            "data": [
                {
                    "normalStock": [{"name": "Rocket-Rocket"}, {"name": "Spin-Spin"}],
                    "mirageStock": [{"name": "Kitsune"}, {"name": "Rocket-Rocket"}],
                }
            ],
        }
        self.assertEqual(
            bot.parse_gamersberg_stock(payload),
            bot.Stock(normal=("Rocket", "Spin"), mirage=("Kitsune", "Rocket")),
        )

    def test_three_json_sources_are_configured(self) -> None:
        sources = bot.stock_sources()
        self.assertEqual(len(sources), 3)
        self.assertTrue(all(source.url.startswith("https://") for source in sources))

    def test_all_source_failures_produce_a_clear_error(self) -> None:
        source = bot.ApiSource("Unavailable", "https://example.invalid", bot.parse_stock, {})
        with (
            patch.object(bot, "stock_sources", return_value=(source, source, source)),
            patch.object(bot, "read_json", side_effect=bot.BotError("network unavailable")),
            self.assertRaisesRegex(bot.BotError, "All 3 public stock APIs failed"),
        ):
            bot.get_current_stock()

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
