
from redisvl.schema import IndexSchema

INDEX_NAME = "blue_horizon_knowledge"
INDEX_PREFIX = "bh_doc"
EMBEDDING_DIMS = 1536


def build_redis_schema() -> IndexSchema:
    return IndexSchema.from_dict({
        "index": {"name": INDEX_NAME, "prefix": INDEX_PREFIX},
        "fields": [
       
            {"type": "tag", "name": "id"},
            {"type": "tag", "name": "doc_id"},
            {"type": "text", "name": "text"},
            {
                "type": "vector",
                "name": "vector",
                "attrs": {
                    "dims": EMBEDDING_DIMS,
                    "algorithm": "hnsw",
                    "distance_metric": "cosine",
                },
            },
            {"type": "tag", "name": "source"},
            {"type": "tag", "name": "category"},
        ],
    })