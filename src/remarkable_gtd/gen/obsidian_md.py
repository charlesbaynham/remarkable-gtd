"""Obsidian-flavoured markdown to HTML for the reference-note renderer.

Wiki-links print as plain underlined text; ``![[embeds]]`` and local
``![](images)`` are handed to a resolver (the renderer inlines attachments
as data URIs). Raw HTML other than ``<br>`` is escaped, so a note cannot
inject markup into the page.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from urllib.parse import unquote

from markdown_it import MarkdownIt
from markdown_it.common.utils import escapeHtml
from markdown_it.renderer import RendererHTML
from markdown_it.rules_inline import StateInline
from markdown_it.token import Token
from markdown_it.utils import EnvType

FRONT_MATTER = re.compile(r"\A---[ \t]*\n.*?\n---[ \t]*(?:\n|\Z)", re.DOTALL)
_WIKILINK = re.compile(r"(!?)\[\[([^\[\]\n]+?)\]\]")
_TASK_MARK = re.compile(r"\[([ xX])\]\s+")
_BR = re.compile(r"<br\s*/?>", re.IGNORECASE)

EmbedResolver = Callable[[str, int | None], str]


def strip_front_matter(text: str) -> str:
    return FRONT_MATTER.sub("", text, count=1)


def _wikilink_rule(state: StateInline, silent: bool) -> bool:
    match = _WIKILINK.match(state.src, state.pos)
    if not match:
        return False
    if not silent:
        target, _, alias = match[2].partition("|")
        token = state.push("wikilink", "", 0)
        token.meta = {"embed": bool(match[1]), "target": target.strip(), "alias": alias.strip()}
    state.pos = match.end()
    return True


def _task_list_rule(state) -> None:
    tokens = state.tokens
    for i in range(2, len(tokens)):
        inline = tokens[i]
        if (inline.type != "inline" or tokens[i - 1].type != "paragraph_open"
                or tokens[i - 2].type != "list_item_open"):
            continue
        match = _TASK_MARK.match(inline.content)
        children = inline.children or []
        if not match or not children or children[0].type != "text":
            continue
        inline.content = inline.content[match.end():]
        children[0].content = children[0].content[match.end():]
        box = Token("checkbox", "", 0)
        box.meta = {"done": match[1] != " "}
        children.insert(0, box)
        tokens[i - 2].attrJoin("class", "task")


def _escape_html(renderer: RendererHTML, tokens, idx, options, env) -> str:
    raw = tokens[idx].content
    if _BR.fullmatch(raw.strip()):
        return "<br>"
    return escapeHtml(raw)


def _is_local(src: str) -> bool:
    return not re.match(r"[a-z][a-z0-9+.-]*:", src, re.IGNORECASE)


def build_parser(resolve_embed: EmbedResolver) -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": True}).enable(["table", "strikethrough"])
    md.inline.ruler.before("link", "wikilink", _wikilink_rule)
    md.core.ruler.after("inline", "task_lists", _task_list_rule)

    def wikilink(renderer, tokens, idx, options, env: EnvType) -> str:
        meta = tokens[idx].meta
        if meta["embed"]:
            width = int(meta["alias"]) if meta["alias"].isdigit() else None
            return resolve_embed(meta["target"], width)
        return f'<span class="wl">{escapeHtml(meta["alias"] or meta["target"])}</span>'

    def image(renderer, tokens, idx, options, env: EnvType) -> str:
        token = tokens[idx]
        src = token.attrGet("src") or ""
        name = token.content or src
        if _is_local(src):
            return resolve_embed(unquote(src).rsplit("/", 1)[-1], None)
        return f'<span class="att">image: {escapeHtml(name)}</span>'

    def checkbox(renderer, tokens, idx, options, env: EnvType) -> str:
        return f'<span class="chk{" done" if tokens[idx].meta["done"] else ""}"></span>'

    md.add_render_rule("wikilink", wikilink)
    md.add_render_rule("checkbox", checkbox)
    md.add_render_rule("image", image)
    md.add_render_rule("html_inline", _escape_html)
    md.add_render_rule("html_block", _escape_html)
    return md


def render_markdown(text: str, resolve_embed: EmbedResolver) -> str:
    return build_parser(resolve_embed).render(strip_front_matter(text))
