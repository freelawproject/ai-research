"""Q5a — Map each duplicate cluster to its opinion IDs (for embedding lookup).

Run inside the cl-django container (needs q4_duplicate_groups.json present):
    docker exec cl-django python manage.py shell -c \
        "exec(open('/tmp/appellate_chain/q5a_map_opinion_ids.py').read())"

Embeddings in S3 are keyed by opinion_id, but duplicates are detected at the
cluster level. This emits cluster_id -> [opinion_id, ...] (ordered) so the
standalone Q5b step can fetch the right embedding files.

Writes q5_opinion_ids.json:
  {"<cluster_id>": [op_id, op_id, ...], ...}
"""

import json
import os

from cl.search.models import Opinion

OUT_DIR = "/tmp/appellate_chain"


def main():
    with open(os.path.join(OUT_DIR, "q4_duplicate_groups.json")) as f:
        groups = json.load(f)

    cluster_ids = set()
    for side in ("scotus", "circuits"):
        for g in groups.get(side, []):
            cluster_ids.update(g["cluster_ids"])
    print(f"{len(cluster_ids)} distinct clusters in duplicate groups")

    mapping = {}
    qs = (Opinion.objects
          .filter(cluster_id__in=cluster_ids)
          .order_by("cluster_id", "id")
          .values_list("cluster_id", "id")
          .iterator(chunk_size=10000))
    for cluster_id, op_id in qs:
        mapping.setdefault(str(cluster_id), []).append(op_id)

    missing = [c for c in cluster_ids if str(c) not in mapping]
    print(f"clusters with opinions: {len(mapping)}; "
          f"clusters with no opinion rows: {len(missing)}")

    out = os.path.join(OUT_DIR, "q5_opinion_ids.json")
    with open(out, "w") as f:
        json.dump(mapping, f)
    print("wrote", out)


main()
