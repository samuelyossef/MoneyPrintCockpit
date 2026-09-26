"""Shared writing guidance and explainable narration diagnostics."""
from __future__ import annotations

import re
from collections import Counter


def writing_brief(format: str) -> str:
    structure = (
        "Abra na primeira frase com uma situação concreta, contraste ou pergunta específica; "
        "entregue a resposta prometida antes do final. Desenvolva uma ideia central, com exemplo "
        "visual e consequência. Termine com uma conclusão útil, sem repetir a abertura."
        if format == "short" else
        "Abra com a pergunta central e por que ela importa. Construa seções com novas informações, "
        "exemplos concretos e transições causais. Retome perguntas abertas e conclua com uma síntese útil."
    )
    return (
        structure + " Escreva para ser ouvido: uma ideia por frase, variação de ritmo, pausas por "
        "pontuação e vocabulário natural no idioma do canal. Prefira frases curtas sem deixar o texto "
        "telegráfico. Escreva números, unidades e siglas na forma falada quando isso evitar ambiguidade "
        "de pronúncia, preservando seu significado. Não inclua SSML nem instruções de entonação no texto. "
        "Evite saudações genéricas, enchimento, promessas exageradas e pedidos repetidos para seguir o canal. "
        "Não invente estatísticas, estudos, citações, fontes ou acontecimentos recentes; qualifique "
        "incertezas e não apresente ficção ou metáfora como fato. Cada trecho deve acrescentar algo "
        "e sugerir uma situação que possa ser mostrada. Respeite o estilo e as instruções do canal."
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
