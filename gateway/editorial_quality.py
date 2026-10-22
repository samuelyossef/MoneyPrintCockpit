"""Shared writing guidance and explainable narration diagnostics."""
from __future__ import annotations

import re
from collections import Counter


def writing_brief(format: str) -> str:
    structure = (
        "Open the first sentence with a concrete situation, contrast, or specific question; "
        "deliver the promised answer before the end. Develop one central idea, with a visual "
        "example and a consequence. End with a useful conclusion, without repeating the opening."
        if format == "short" else
        "Open with the central question and why it matters. Build sections with new information, "
        "concrete examples, and causal transitions. Return to open questions and close with a useful synthesis."
    )
    return (
        structure + " Write to be heard, in the channel's language: one idea per sentence, varied rhythm, pauses through "
        "punctuation, and natural vocabulary. Prefer short sentences without making the text "
        "telegraphic. Write numbers, units, and abbreviations in spoken form when that avoids ambiguous "
        "pronunciation, preserving their meaning. Do not include SSML or intonation instructions in the text. "
        "Avoid generic greetings, filler, exaggerated promises, and repeated requests to follow the channel. "
        "Do not invent statistics, studies, quotations, sources, or recent events; qualify "
        "uncertainty and do not present fiction or metaphor as fact. Each passage should add something "
        "and suggest a situation that can be shown. Respect the channel's style and instructions."
        " Check spelling, accents, grammar, agreement, and punctuation in the requested language; "
        "do a final read-aloud pass and prefer natural, unambiguous forms for the narration."
    )


def narration_diagnostics(script: str) -> dict:
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", script) if part.strip()]
    counts = [len(sentence.split()) for sentence in sentences]
    normalized = [re.sub(r"\W+", " ", sentence.casefold()).strip() for sentence in sentences]
    duplicates = [sentence for sentence, count in Counter(normalized).items() if count > 1 and len(sentence.split()) >= 5]
    return {
        "words": len(script.split()),
        "sentences": len(sentences),
        "long_sentences": sum(count > 32 for count in counts),
        "repeated_sentences": duplicates[:5],
        "average_sentence_words": round(sum(counts) / max(1, len(counts)), 1),
    }
