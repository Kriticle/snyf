from __future__ import annotations

import os

from functions.defaults import embed as local_embed
from functions.fake_llm import enrich as fake_enrich
from functions.fake_llm import extract_query_attributes as fake_extract_query_attributes


def enrich_fn():
    if provider_name() == "openai":
        from functions.openai_llm import OpenAILLM

        return OpenAILLM().enrich
    return fake_enrich


def query_attribute_fn():
    if provider_name() == "openai":
        from functions.openai_llm import OpenAILLM

        return OpenAILLM().extract_query_attributes
    return fake_extract_query_attributes


def embed_fn():
    if provider_name() == "openai":
        from functions.openai_llm import OpenAILLM

        return OpenAILLM().embed
    return local_embed


def provider_name() -> str:
    return os.getenv("LLM_PROVIDER", "fake").strip().lower()
