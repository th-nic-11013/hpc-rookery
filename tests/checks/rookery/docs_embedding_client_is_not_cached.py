from unittest.mock import patch

LABEL = 'docs embedding credentials are refreshed per search'


def docs_embedding_client_is_not_cached():
    from hpc_agent_core.docs_server import _index

    index = _index("rikyu")
    assert index._embed_client is None, (
        "the cached docs index captured an embedding client/API key"
    )
    token = object()
    # search() only reaches the vector path when embeddings are loaded.
    # A facility may legitimately ship BM25-only (no embeddings.npy, e.g.
    # after a guide edit made while the embedding endpoint was down), so
    # stand in a matrix rather than depending on what happens to be
    # committed — the invariant under test is that the client is passed
    # per call and never cached on the index.
    import numpy as _np
    with patch.object(index, "_embeddings",
                      _np.zeros((len(index.chunks), 4), dtype="float32")), \
            patch.object(index, "_vector_search", return_value=[]) as vector:
        index.search("anything", embed_client=token)
    vector.assert_called_once_with("anything", 5, token)
