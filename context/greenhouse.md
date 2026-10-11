# Greenhouse description

Human-authored. The agent reads this first. Items marked TODO are unknown; the
agent must not guess them and should ask the grower instead.

## Site
- Climate: USDA hardiness zone 6b (average annual minimum about -5 to 0 °F /
  -20 to -18 °C). Cold nights and a real winter heating load; hot, humid summers.
- Location (latitude / longitude): TODO (needed for sun angle and weather).
- Structure, glazing, size: TODO.

## Crop and layout
- Hanging baskets, 4 rows of about 30 baskets each (roughly 120 in total).
- Rows run from the southeast end to the northwest end of the greenhouse.
  Basket positions are numbered 1 to ~30 starting at the southeast end.
- Crop, growth stage and schedule: TODO.
- See `basket_map.yaml` for the row/position model and `targets.yaml` for targets.

## Things the agent should keep in mind
- Rows differ in light, temperature and airflow. A reading from one row does not
  describe the others until the data shows it.
- Sensor-to-row mapping is not yet known (TODO). Until it is, state which
  sensor an observation comes from rather than saying "the greenhouse".
- Zone 6b is the outdoor climate. Do not call a greenhouse area a "zone 6b zone".
