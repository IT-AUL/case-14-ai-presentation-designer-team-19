"""Provider gateway — frozen interface."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel


class ModelGateway(Protocol):
    async def text_json(
        self, prompt_name: str, payload: dict, schema: type[BaseModel]
    ) -> BaseModel: ...

    async def vision_json(
        self,
        prompt_name: str,
        images: list[str],
        payload: dict,
        schema: type[BaseModel],
    ) -> BaseModel: ...

    async def embed(self, texts: list[str]) -> list[list[float]]: ...

    async def image_generate(self, prompt: str, *, size: str = "1024x1024") -> bytes: ...


def json_schema_of(model: type[BaseModel]) -> dict[str, Any]:
    """JSON Schema for OpenAI-compatible `response_format: json_schema`.

    Строгая форма Structured Outputs: рекурсивно у каждого record-объекта
    все ключи ``properties`` объявлены ``required`` и ``additionalProperties``
    закрыт. Pydantic-модели не меняются — опциональные поля остаются
    ``anyOf[..., {type: null}]`` и принимают ``null``, что и требует strict.
    """
    schema = model.model_json_schema()
    _strictify(schema)
    return {
        "name": model.__name__,
        "schema": schema,
        "strict": True,
    }


def _strictify(node: Any) -> None:
    """Post-order walk: у record-объектов required=all, additionalProperties=false.

    Свободные map'ы (``type: object`` без ``properties`` или со схемой в
    ``additionalProperties``) не закрываются — выставить ``false`` молча
    поменяло бы их семантику.
    """
    if isinstance(node, dict):
        for value in node.values():
            _strictify(value)
        props = node.get("properties")
        if isinstance(props, dict):
            node["required"] = list(props)
            if not isinstance(node.get("additionalProperties"), dict):
                node["additionalProperties"] = False
    elif isinstance(node, list):
        for item in node:
            _strictify(item)
