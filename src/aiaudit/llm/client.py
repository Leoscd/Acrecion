"""Cliente LLM unico. OpenAI por defecto; Ollama via su endpoint compatible con OpenAI.

Usa Structured Outputs: el modelo responde con JSON validado contra un modelo pydantic.
Registra cada llamada (prompt, tokens, costo estimado) en interim/llm_log.jsonl para auditar
que no se envio PII y para cotizar el costo del diagnostico.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import TypeVar

from dotenv import load_dotenv
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLM:
    def __init__(self, cfg: dict, log_path: Path | None = None):
        from openai import OpenAI

        load_dotenv()
        self.cfg = cfg
        self.log_path = log_path
        self.tokens_in = self.tokens_out = 0
        if cfg["provider"] == "ollama":
            self.client = OpenAI(base_url=cfg.get("base_url") or "http://localhost:11434/v1", api_key="ollama")
        else:
            if not os.getenv("OPENAI_API_KEY"):
                raise RuntimeError("Falta OPENAI_API_KEY (definir en .env o en el entorno)")
            self.client = OpenAI(base_url=cfg.get("base_url"))

    def estructurado(self, sistema: str, usuario: str, esquema: type[T]) -> T:
        kwargs = {"temperature": self.cfg["temperature"]} if self.cfg.get("temperature") is not None else {}
        t0 = time.time()
        r = self.client.chat.completions.parse(
            model=self.cfg["model"],
            messages=[{"role": "system", "content": sistema}, {"role": "user", "content": usuario}],
            response_format=esquema,
            **kwargs,
        )
        msg = r.choices[0].message
        if msg.refusal or msg.parsed is None:
            raise RuntimeError(f"El modelo no devolvio una respuesta valida: {msg.refusal or r.choices[0].finish_reason}")
        if r.usage:
            self.tokens_in += r.usage.prompt_tokens
            self.tokens_out += r.usage.completion_tokens
        self._registrar(sistema, usuario, msg.parsed, r.usage, time.time() - t0)
        return msg.parsed

    @property
    def costo_usd(self) -> float:
        return (self.tokens_in * self.cfg.get("precio_usd_1m_input", 0)
                + self.tokens_out * self.cfg.get("precio_usd_1m_output", 0)) / 1e6

    def _registrar(self, sistema, usuario, salida, usage, segundos):
        if not self.log_path:
            return
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "modelo": self.cfg["model"], "segundos": round(segundos, 1),
                "tokens_in": getattr(usage, "prompt_tokens", None), "tokens_out": getattr(usage, "completion_tokens", None),
                "sistema": sistema, "usuario": usuario, "salida": salida.model_dump(),
            }, ensure_ascii=False) + "\n")
