"""Sugestão de categoria por aprendizado com o histórico (Naive Bayes multinomial).

Puro e determinístico: recebe exemplos (descrição, categoria) já lançados pela própria
organização e devolve a categoria mais provável para uma descrição nova. Não chama
serviços externos e nada sai da organização. Só sugere quando há evidência suficiente;
a decisão final é sempre de quem está lançando.
"""

import math
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Hashable, Iterable
from dataclasses import dataclass, field

STOPWORDS = frozenset(
    "a o os as de da do das dos e em no na nos nas para por com sem um uma ao aos pela "
    "pelo ref referente pagamento pgto pag compra transf transferencia ted doc pix".split()
)
MIN_EXAMPLES = 3
MIN_CONFIDENCE = 0.6


def tokenize(text: str) -> list[str]:
    """Minúsculas, sem acentos, sem números e sem palavras vazias ("Pgto Luz 03/26" -> luz)."""
    plain = unicodedata.normalize("NFKD", text.lower())
    plain = "".join(char for char in plain if not unicodedata.combining(char))
    return [token for token in re.findall(r"[a-z]{2,}", plain) if token not in STOPWORDS]


@dataclass
class CategoryModel[Label: Hashable]:
    documents: Counter = field(default_factory=Counter)
    token_counts: dict = field(default_factory=lambda: defaultdict(Counter))
    vocabulary: set[str] = field(default_factory=set)

    @property
    def size(self) -> int:
        return sum(self.documents.values())


@dataclass(frozen=True, slots=True)
class Suggestion[Label: Hashable]:
    label: Label
    confidence: float
    examples: int


def train[Label: Hashable](examples: Iterable[tuple[str, Label]]) -> CategoryModel[Label]:
    model: CategoryModel[Label] = CategoryModel()
    for description, label in examples:
        tokens = tokenize(description)
        if not tokens:
            continue
        model.documents[label] += 1
        model.token_counts[label].update(tokens)
        model.vocabulary.update(tokens)
    return model


def suggest[Label: Hashable](
    model: CategoryModel[Label],
    description: str,
    *,
    min_examples: int = MIN_EXAMPLES,
    min_confidence: float = MIN_CONFIDENCE,
) -> Suggestion[Label] | None:
    """Categoria mais provável, ou None quando o histórico não sustenta uma sugestão."""
    tokens = [token for token in tokenize(description) if token in model.vocabulary]
    if not tokens or model.size < min_examples:
        return None
    vocabulary = len(model.vocabulary)
    scores: dict[Label, float] = {}
    for label, documents in model.documents.items():
        counts = model.token_counts[label]
        total = sum(counts.values())
        score = math.log(documents / model.size)
        for token in tokens:
            score += math.log((counts[token] + 1) / (total + vocabulary))
        scores[label] = score
    best = max(scores, key=scores.__getitem__)
    peak = scores[best]
    confidence = 1 / sum(math.exp(score - peak) for score in scores.values())
    if confidence < min_confidence:
        return None
    return Suggestion(best, round(confidence, 2), model.documents[best])
