# Greenhouse description

Human-authored. The agent reads this first. Items marked TODO are unknown; the
agent must not guess them and should ask the grower instead.

## Site
- Climate: USDA hardiness zone 6b (average annual minimum about -5 to 0 °F /
  -20 to -18 °C). Cold nights and a real winter heating load; hot, humid summers.
- Location (latitude / longitude): TODO (needed for sun angle and weather).
- Structure: hoop greenhouse with an inflated (double-layer) plastic roof and
  plastic siding. The north-facing wall is insulated. Size: TODO.
- Inflated plastic roofs depend on the blower: a lost or weak blower means
  lost insulation and a faster heat loss at night. Watch for it.

## Crop and layout
- Hanging baskets, 4 rows of about 30 baskets each (roughly 120 in total).
- Rows run from the southeast end to the northwest end of the greenhouse.
  Basket positions are numbered 1 to ~30 starting at the southeast end.
- Crop: mostly petunias. Season runs from about February to about May.
  Early season (Feb to Mar) is the cold, heating-dominated period; late season
  (Apr to May) shifts to heat, ventilation and humidity control.
- Growth stage by date and exact varieties: TODO.
- See `basket_map.yaml` for the row/position model and `targets.yaml` for targets.

## Things the agent should keep in mind
- Rows differ in light, temperature and airflow. A reading from one row does not
  describe the others until the data shows it.
- There are no per-row sensors. Sensors sit in the center of the greenhouse
  only. Row differences (especially near the insulated north wall versus the
  south, and the ends) are not measured; say so, and treat any claim about a
  specific row as an inference, not a measurement.
- Zone 6b is the outdoor climate. Do not call a greenhouse area a "zone 6b zone".
