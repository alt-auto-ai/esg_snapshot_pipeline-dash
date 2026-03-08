import csv
from pathlib import Path


CSV_PATH = Path(__file__).resolve().parent / "7_esg_relevance.csv"

REQUIRED_COLUMNS = [
	"Cross_sector_relevance",
	"Policy_significance",
	"Business_risk_opportunity",
	"Strategic_ESG_signal",
	"Corporate_governance_relevance",
	"Forward_looking_insight",
	"Member Relevance",
]


def parse_rating(value: str) -> float:
	try:
		return float((value or "").strip())
	except Exception:
		return 0.0


def compute_weighted_rating(row: dict) -> float:
	h = parse_rating(row.get("Cross_sector_relevance", ""))
	i = parse_rating(row.get("Policy_significance", ""))
	j = parse_rating(row.get("Business_risk_opportunity", ""))
	k = parse_rating(row.get("Strategic_ESG_signal", ""))
	l = parse_rating(row.get("Corporate_governance_relevance", ""))
	m = parse_rating(row.get("Forward_looking_insight", ""))
	n = parse_rating(row.get("Member Relevance", ""))
	return (h * 1 + i * 1 + j * 1 + k * 0.9 + l * 0.9 + m * 0.9 + n * 0.2) / 5.9


def relevance_label(score: float) -> str:
	if score >= 7:
		return "High"
	if score >= 5:
		return "Medium"
	return "Low"


def main() -> None:
	if not CSV_PATH.exists():
		raise SystemExit(f"[ERROR] CSV not found: {CSV_PATH}")

	with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as f:
		reader = csv.DictReader(f)
		fieldnames = reader.fieldnames or []
		rows = list(reader)

	missing = [col for col in REQUIRED_COLUMNS if col not in fieldnames]
	if missing:
		raise SystemExit(f"[ERROR] Missing required columns: {', '.join(missing)}")

	output_fieldnames = list(fieldnames)
	if "Rating" not in output_fieldnames:
		output_fieldnames.append("Rating")
	if "Relevance" not in output_fieldnames:
		output_fieldnames.append("Relevance")

	for row in rows:
		score = compute_weighted_rating(row)
		row["Rating"] = f"{score:.6f}".rstrip("0").rstrip(".")
		row["Relevance"] = relevance_label(score)

	with CSV_PATH.open("w", encoding="utf-8", newline="") as f:
		writer = csv.DictWriter(f, fieldnames=output_fieldnames)
		writer.writeheader()
		writer.writerows(rows)

	print(f"[DONE] Updated {CSV_PATH} with 'Rating' column.")


if __name__ == "__main__":
	main()
