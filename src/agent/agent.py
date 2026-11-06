"""Step 4 — the agent loop.

Runs a manual Anthropic tool-use loop: Claude plans across phases A/B/C, calls
the Phase B tools to read the guide, and finally emits structure_map.json. We
use the manual loop (rather than the beta tool runner) so we own the loop, can
cap iterations, and can capture the final JSON message directly.

Requires ANTHROPIC_API_KEY (or an `ant auth login` profile). Model defaults to
claude-opus-5-5; override with ANTHROPIC_MODEL.
"""

from __future__ import annotations

import json
from pathlib import Path

import config
from src.agent.prompts import SYSTEM_PROMPT, build_user_message
from src.guide.store import GuideStore
from src.guide.tools import TOOL_DEFINITIONS, GuideTools

try:
    from src.terminology.tools import TERMINOLOGY_TOOL_DEFINITIONS, TerminologyTools
except Exception:  # module optional; agent still runs with guide tools only
    TERMINOLOGY_TOOL_DEFINITIONS, TerminologyTools = [], None


def _strip_json(text: str) -> str:
    """Tolerate an accidental markdown fence around the final JSON."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.endswith("```"):
            t = t[: -3]
        if t.lstrip().startswith("json"):
            t = t.lstrip()[4:]
    return t.strip()


def run(
    source_metadata: dict | str | Path = config.SOURCE_METADATA_PATH,
    *,
    model: str = config.AGENT_MODEL,
    max_tokens: int = config.AGENT_MAX_TOKENS,
    max_turns: int = config.AGENT_MAX_TURNS,
    store: GuideStore | None = None,
    entities: list[str] | None = None,
    verbose: bool = True,
) -> dict:
    """Run the agent and return the parsed structure_map dict.

    entities: optional list of entity names to scope the mapping to (keeps the
    output small and cheap; recommended for a single-entity end-to-end run).
    """
    import anthropic

    if isinstance(source_metadata, (str, Path)):
        metadata_json = Path(source_metadata).read_text(encoding="utf-8")
    else:
        metadata_json = json.dumps(source_metadata, ensure_ascii=False, indent=2)

    tools = GuideTools(store=store)
    # Terminology tool (sugerir_conceptos) — verified codes from Termbox. Optional.
    term = None
    tool_defs = list(TOOL_DEFINITIONS)
    if TerminologyTools is not None:
        try:
            term = TerminologyTools()
            tool_defs += TERMINOLOGY_TOOL_DEFINITIONS
        except Exception as exc:  # keep the agent usable without terminology
            if verbose:
                print(f"  [warn] terminology tool unavailable: {exc}")
            term = None

    client = anthropic.Anthropic()

    messages = [{"role": "user", "content": build_user_message(metadata_json, entities)}]
    final_text = None

    for turn in range(1, max_turns + 1):
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=SYSTEM_PROMPT,
            thinking={"type": "adaptive"},
            tools=tool_defs,
            messages=messages,
        )

        if response.stop_reason == "tool_use":
            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
            if verbose:
                for b in tool_use_blocks:
                    print(f"  [turn {turn}] -> {b.name}({json.dumps(b.input, ensure_ascii=False)})")
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for b in tool_use_blocks:
                if term is not None and term.handles(b.name):
                    result = term.dispatch(b.name, b.input)
                else:
                    result = tools.dispatch(b.name, b.input)
                is_error = result.lstrip().startswith('{"error"')
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": b.id,
                        "content": result,
                        "is_error": is_error,
                    }
                )
            messages.append({"role": "user", "content": tool_results})
            continue

        # end_turn (or any non-tool stop): capture the final text.
        final_text = "".join(b.text for b in response.content if b.type == "text")
        if verbose:
            print(f"  [turn {turn}] final message ({response.stop_reason})")
        if response.stop_reason == "max_tokens":
            # Save the truncated output for inspection, then fail with guidance.
            try:
                (config.STRUCTURE_MAP_PATH.with_suffix(".partial.txt")).write_text(
                    final_text, encoding="utf-8")
            except Exception:
                pass
            raise RuntimeError(
                f"the model hit max_tokens ({max_tokens}) — the output was truncated. "
                "Scope the run to fewer entities (e.g. `--entity Patient`) or raise "
                "AGENT_MAX_TOKENS. A truncated draft was saved alongside structure_map.json."
            )
        break
    else:
        raise RuntimeError(f"agent did not finish within {max_turns} turns")

    if not final_text:
        raise RuntimeError("agent produced no final text message")

    try:
        return json.loads(_strip_json(final_text))
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"agent final message was not valid JSON: {exc}\n---\n{final_text[:2000]}"
        ) from exc


def run_to_file(
    source_metadata: dict | str | Path = config.SOURCE_METADATA_PATH,
    out_path: Path | str = config.STRUCTURE_MAP_PATH,
    **kwargs,
) -> Path:
    data = run(source_metadata, **kwargs)
    out_path = Path(out_path)
    out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return out_path


if __name__ == "__main__":
    path = run_to_file()
    print(f"structure_map written to {path}")
