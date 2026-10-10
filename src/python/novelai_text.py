"""
NovelAI公式のストーリー執筆機能(text.novelai.net)をKayra(kayra-v1)で叩くための薄いラッパー。

novelai-sdk (0.8.1) は画像生成専用で、テキスト生成には対応していないため、ここでは
Aedial/novelai-api (https://github.com/Aedial/novelai-api) の実装を参考に、直接
httpx でリクエストを組み立てる。トークナイザーは NovelAI が GPLv2 で公式公開している
nerdstash_v2 (assets/tokenizers/nerdstash_v2.model) をそのまま使う。
"""

from __future__ import annotations

import base64
import itertools
import re
from pathlib import Path
from typing import Any

import httpx
import sentencepiece
from novelai import AsyncNovelAI

_TOKENIZER_PATH = Path(__file__).resolve().parent.parent.parent / "assets" / "tokenizers" / "nerdstash_v2.model"
_TEXT_API_ADDRESS = "https://text.novelai.net"

KAYRA_MODEL = "kayra-v1"


class _NerdstashTokenizer:
    """sentencepieceの特殊トークン(<|unk|>等)をNovelAI互換の記法で扱うラッパー。"""

    def __init__(self, model_path: Path):
        self._sp = sentencepiece.SentencePieceProcessor()
        self._sp.Load(str(model_path))

        self._ids_to_special = {
            self._sp.unk_id(): "<|unk|>",
            self._sp.pad_id(): "<|pad|>",
            self._sp.bos_id(): "<|startoftext|>",
            self._sp.eos_id(): "<|endoftext|>",
        }
        self._special_to_ids = {v: k for k, v in self._ids_to_special.items()}
        keys = "|".join(re.escape(k) for k in self._special_to_ids)
        self._special_regex = re.compile(keys) if keys else None

    def encode(self, text: str) -> list[int]:
        if self._special_regex is None:
            return self._sp.EncodeAsIds(text)

        matches = list(self._special_regex.finditer(text))
        if not matches:
            return self._sp.EncodeAsIds(text)

        parts = [
            text[0 : matches[0].start()],
            *[text[a.end() : b.start()] for a, b in zip(matches, matches[1:])],
            text[matches[-1].end() :],
        ]
        encoded_parts = [self._sp.EncodeAsIds(p) for p in parts]
        junctions = [self._special_to_ids[m.group(0)] for m in matches]

        result = list(encoded_parts[0])
        for junction, part in zip(junctions, encoded_parts[1:]):
            result.append(junction)
            result.extend(part)
        return result

    def decode(self, tokens: list[int]) -> str:
        if not self._ids_to_special:
            return self._sp.DecodeIds(tokens)

        indexes = [i for i, t in enumerate(tokens) if t in self._ids_to_special]
        if not indexes:
            return self._sp.DecodeIds(tokens)

        parts = [
            tokens[0 : indexes[0]],
            *[tokens[a + 1 : b] for a, b in zip(indexes, indexes[1:])],
            tokens[indexes[-1] + 1 :],
        ]
        decoded_parts = [self._sp.DecodeIds(p) for p in parts]
        junctions = [self._ids_to_special[tokens[i]] for i in indexes]

        return "".join(
            itertools.chain.from_iterable(
                itertools.zip_longest(decoded_parts, junctions, fillvalue="")
            )
        )


_tokenizer: _NerdstashTokenizer | None = None


def _get_tokenizer() -> _NerdstashTokenizer:
    global _tokenizer
    if _tokenizer is None:
        _tokenizer = _NerdstashTokenizer(_TOKENIZER_PATH)
    return _tokenizer


def _tokens_to_b64(tokens: list[int]) -> str:
    raw = b"".join(t.to_bytes(2, byteorder="little", signed=False) for t in tokens)
    return base64.b64encode(raw).decode()


def _b64_to_tokens(b64: str) -> list[int]:
    raw = base64.b64decode(b64)
    return [int.from_bytes(raw[i : i + 2], byteorder="little", signed=False) for i in range(0, len(raw), 2)]


# NovelAI公式の"Carefree"プリセット(presets_kayra_v1)相当のサンプラー構成。
# "order"が無いと top_p/top_k 等のフィルタが一切適用されず、65535語彙全体から
# 高温度でサンプリングされて多言語ノイズのような出力になる(実測して確認済み)。
# Order enum値: Temperature=0, Top_K=1, Top_P=2, TFS=3, Top_A=4
_SAMPLING_ORDER = [2, 3, 0, 4, 1]  # top_p -> tfs -> temperature -> top_a -> top_k

# nerdstash_v2用のブラケット禁止トークン(GlobalSettings._BRACKETS["nerdstash_v2"]をそのまま移植)。
_BRACKET_BAD_WORDS = [
    [3], [49356], [1431], [31715], [34387], [20765], [30702], [10691], [49333],
    [1266], [19438], [43145], [26523], [41471], [2936], [85, 85], [49332], [7286], [1115],
]

# 繰り返しペナルティを免除する頻出語(GlobalSettings._REP_PEN_WHITELIST["nerdstash_v2"]と同じ)。
_REP_PEN_WHITELIST_STRINGS = [
    "'", '"', ",", ".", ":", "\n", "-", "*", ")", " the", " a", " an", " and", " or", " not", " no",
    " is", " was", " were", " did", " does", " isn", " wasn", " weren", " didn", " doesn", " him",
    " her", " his", " hers", " their", " its", " could", " couldn", " should", " shouldn", " would",
    " wouldn", " have", " haven", " had", " hadn", " has", " hasn", " can", " cannot", " are", " aren",
    " will", " won", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9", '."', ',"', "====", " ",
    "'t've", "'s", "'t", "'ve", "'n", "'ll", "'d", "'re", "'m",
]


async def generate_kayra(
    client: AsyncNovelAI,
    prompt: str,
    *,
    max_length: int = 60,
    min_length: int = 1,
    temperature: float = 1.35,
    top_p: float = 0.85,
    top_k: int = 15,
    top_a: float = 0.1,
    tail_free_sampling: float = 0.915,
    repetition_penalty: float = 2.8,
    repetition_penalty_range: int = 2048,
    repetition_penalty_slope: float = 0.02,
    repetition_penalty_frequency: float = 0.02,
    repetition_penalty_presence: float = 0.0,
) -> str:
    """
    NovelAI公式のKayra(kayra-v1)にpromptの続きを書かせ、生成された本文だけを返す。

    パラメータのデフォルト値はNovelAI公式の"Carefree"プリセット(presets_kayra_v1)を踏襲している。

    :param client: 認証済みのAsyncNovelAIクライアント(api_keyの取り出しにのみ使う)
    :param prompt: これまでの文脈(呼び出し側でpreambleや既存本文を含めて組み立てる)
    """

    tokenizer = _get_tokenizer()
    tokens = tokenizer.encode(prompt)
    # repetition_penalty_whitelist はフラットなトークンIDのリストを要求する
    # (bad_words_ids は逆にネストしたリストを要求する。実APIで検証済み)。
    rep_pen_whitelist: list[int] = []
    for s in _REP_PEN_WHITELIST_STRINGS:
        rep_pen_whitelist.extend(tokenizer.encode(s))

    parameters: dict[str, Any] = {
        "use_string": False,
        "min_length": min_length,
        "max_length": max_length,
        "temperature": temperature,
        "top_p": top_p,
        "top_k": top_k,
        "top_a": top_a,
        "tail_free_sampling": tail_free_sampling,
        "typical_p": 1.0,
        "repetition_penalty": repetition_penalty,
        "repetition_penalty_range": repetition_penalty_range,
        "repetition_penalty_slope": repetition_penalty_slope,
        "repetition_penalty_frequency": repetition_penalty_frequency,
        "repetition_penalty_presence": repetition_penalty_presence,
        "repetition_penalty_whitelist": rep_pen_whitelist,
        "order": _SAMPLING_ORDER,
        "bad_words_ids": list(_BRACKET_BAD_WORDS),
        "logit_bias_exp": [],
        "generate_until_sentence": True,
        "return_full_text": False,
        "use_cache": False,
        "prefix": "vanilla",
    }
    body = {
        "input": _tokens_to_b64(tokens),
        "model": KAYRA_MODEL,
        "parameters": parameters,
    }

    headers = {"Authorization": f"Bearer {client.api_key}"}
    async with httpx.AsyncClient(headers=headers, timeout=120) as http_client:
        response = await http_client.post(f"{_TEXT_API_ADDRESS}/ai/generate", json=body)
        if response.status_code != 200:
            raise RuntimeError(f"NovelAI text API error {response.status_code}: {response.text}")
        data = response.json()

    output_tokens = _b64_to_tokens(data["output"])
    return tokenizer.decode(output_tokens)
