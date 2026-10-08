import nflreadpy as nfl


def get_espn_to_fantasypros_map():
    ids = nfl.load_ff_playerids()

    mapping = {}

    for row in ids.iter_rows(named=True):
        espn_id = row.get("espn_id")
        fantasypros_id = row.get("fantasypros_id")

        if espn_id is None or fantasypros_id is None:
            continue

        try:
            mapping[str(espn_id)] = int(fantasypros_id)
        except (ValueError, TypeError):
            continue

    return mapping