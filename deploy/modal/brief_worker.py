"""Modal GPU worker — loads Qwen, accepts prompts, returns generated text.

This container spins up ONLY when a Celery task fires for ``professor_brief``.
Stays cold (free) the rest of the time.

Deploy:
    modal deploy deploy/modal/brief_worker.py

Cold start: ~30-60s. Warm calls: ~2s.
"""

# NOTE: set ``brief_via_modal=True`` in settings (career_copilot.config.settings.Settings)
# to enable routing /prof briefs through this worker. By default we fall back to
# Gemini with strict JSON output, so this worker is only a cost optimisation.

from __future__ import annotations

import json
import sys
import time

import modal

from career_copilot.config import get_settings

settings = get_settings()

app = modal.App(settings.modal_app_name)


@app.cls(
    gpu=settings.brief_gpu,
    image=modal.Image.debian_slim(python_version="3.11").pip_install(
        "vllm>=0.5.0", "transformers", "torch", "httpx", "pyyaml", "pydantic"
    ),
    container_idle_timeout=120,
    secrets=[modal.Secret.from_name("career-copilot-env")],
)
class BriefEngine:
    """Modal GPU class — loads Qwen once, serves repeated prompts."""

    @modal.enter()
    def load_model(self) -> None:
        from vllm import LLM, SamplingParams  # type: ignore[import-untyped]

        model_id = f"Qwen/{settings.brief_model.upper()}"
        self.llm = LLM(
            model=model_id,
            tensor_parallel_size=1,
            gpu_memory_utilization=0.85,
            trust_remote_code=True,
        )
        self.sampling_params = SamplingParams(temperature=0.4, max_tokens=800, top_p=0.9)

    @modal.method()
    def generate(self, prompt: str) -> str:
        outputs = self.llm.generate([prompt], self.sampling_params)
        if outputs and outputs[0].outputs:
            return outputs[0].outputs[0].text
        return ""


@app.local_entrypoint()
def local_test() -> None:
    engine = BriefEngine()
    result = engine.generate.remote("Write a one-paragraph research brief about a professor.")
    print(result)


if __name__ == "__main__":
    # CLI bridge used by Celery task: python -m deploy.modal.brief_worker
    from backbone.prompt_registry.loader import load, render

    payload_str = None
    for arg in sys.argv[1:]:
        if arg.startswith("--payload="):
            payload_str = arg.split("=", 1)[1]
        elif arg == "--payload" and len(sys.argv) > sys.argv.index(arg) + 1:
            payload_str = sys.argv[sys.argv.index(arg) + 1]

    if payload_str is None:
        print("Usage: python -m deploy.modal.brief_worker --payload '{...}'")
        sys.exit(1)

    payload = json.loads(payload_str)
    inputs = payload.get("inputs", {})
    prompt_name = payload.get("prompt_name", "professor_brief")
    raw_prompt = payload.get("raw_prompt")

    if raw_prompt:
        rendered_prompt = raw_prompt
    else:
        template = load("paper_tracker", prompt_name)
        rendered_prompt, _ = render(template, inputs)

    engine = BriefEngine()
    start = time.monotonic()
    result = engine.generate.remote(rendered_prompt)
    elapsed = time.monotonic() - start

    print(result)
    print(f"[{elapsed:.1f}s]", file=sys.stderr)
