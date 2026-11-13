"""Embeddings for retrieval.

``LexicalEmbedder`` works everywhere with no API: accent-folded word unigrams and
bigrams hashed into a fixed-size vector (cosine = weighted token overlap).
``OpenAIEmbedder`` is used when an OpenAI key is available and ``KB_EMBEDDINGS``
allows it; both produce plain ``list[float]`` so retrieval code never cares.
"""

from __future__ import annotations

import hashlib
import itertools
import math
import re
from typing import Protocol

import httpx

_ACCENTS = str.maketrans(
    "àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ",
    "aaaaaaaaaaaaaaaaaeeeeeeeeeeeiiiiiooooooooooooooooouuuuuuuuuuuyyyyyd",
)
_STOP = {
    "la",
    "va",
    "cua",
    "co",
    "cho",
    "voi",
    "o",
    "den",
    "tu",
    "nhu",
    "thi",
    "ma",
    "de",
    "khong",
    "nao",
    "gi",
    "the",
    "ban",
    "minh",
    "em",
    "anh",
    "chi",
    "a",
    "ạ",
    "da",
    "oi",
    "nhe",
    "nay",
    "do",
    "cac",
    "nhung",
    "mot",
    "bao",
    "nhieu",
    "hoi",
    "muon",
    "and",
    "of",
    "to",
    "is",
    "in",
    "for",
}


def fold(text: str) -> str:
    return text.lower().translate(_ACCENTS)


def tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", fold(text))
    return [w for w in words if len(w) > 1 and w not in _STOP]


class Embedder(Protocol):
    name: str

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class LexicalEmbedder:
    name = "lexical"
    dim = 2048

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        toks = tokens(text)
        feats = toks + [f"{a}_{b}" for a, b in itertools.pairwise(toks)]
        for f in feats:
            h = int(hashlib.blake2b(f.encode(), digest_size=4).hexdigest(), 16)
            vec[h % self.dim] += 1.0 if "_" not in f else 1.5  # bigrams carry more meaning
        norm = math.sqrt(sum(v * v for v in vec))
        return [v / norm for v in vec] if norm else vec

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]


class OpenAIEmbedder:
    def __init__(
        self,
        api_key: str,
        model: str = "text-embedding-3-small",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.name = f"openai:{model}"
        self._transport = transport

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        async with httpx.AsyncClient(timeout=60.0, transport=self._transport) as http:
            for i in range(0, len(texts), 64):
                batch = texts[i : i + 64]
                r = await http.post(
                    "https://api.openai.com/v1/embeddings",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={"model": self.model, "input": batch},
                )
                if r.status_code != 200:
                    raise RuntimeError(f"embeddings API error {r.status_code}: {r.text[:200]}")
                data = sorted(r.json()["data"], key=lambda d: d["index"])
                out.extend([d["embedding"] for d in data])
        return out


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    num = sum(x * y for x, y in zip(a, b, strict=False))
    da = math.sqrt(sum(x * x for x in a))
    db = math.sqrt(sum(y * y for y in b))
    return num / (da * db) if da and db else 0.0
