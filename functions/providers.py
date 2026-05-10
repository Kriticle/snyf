from __future__ import annotations

import os
from functools import lru_cache

from functions.defaults import embed as local_embed
from functions.fake_llm import enrich as fake_enrich
from functions.fake_llm import extract_query_attributes as fake_extract_query_attributes


def enrich_fn():
    if provider_name() == "openai":
        return openai_llm().enrich
    return fake_enrich


def query_attribute_fn():
    if provider_name() == "openai":
        return openai_llm().extract_query_attributes
    return fake_extract_query_attributes


def embed_fn():
    if provider_name() == "openai":
        return openai_llm().embed
    return local_embed


def embed_many_fn():
    if provider_name() == "openai":
        return openai_llm().embed_many

    def embed_many(texts: list[str]) -> list[list[float]]:
        return [local_embed(text) for text in texts]

    return embed_many


@lru_cache(maxsize=1)
def openai_llm():
    from functions.openai_llm import OpenAILLM

    return OpenAILLM()


def provider_name() -> str:
    return os.getenv("LLM_PROVIDER", "fake").strip().lower()
