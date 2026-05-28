from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path


class SimpleCLIPTokenizer:
    """Small CLIP BPE tokenizer fallback for local tokenizer.json files."""

    def __init__(self, tokenizer_json_path: str | Path, *, sequence_length: int = 77):
        payload = json.loads(Path(tokenizer_json_path).read_text(encoding="utf-8"))
        model = payload["model"]
        self.vocab: dict[str, int] = {str(key): int(value) for key, value in model["vocab"].items()}
        self.bpe_ranks = {
            tuple(merge.split()): rank
            for rank, merge in enumerate(model.get("merges", []))
            if merge and not merge.startswith("#")
        }
        self.byte_encoder = bytes_to_unicode()
        self.sequence_length = int(sequence_length)
        self.start_id = self.vocab["<|startoftext|>"]
        self.end_id = self.vocab["<|endoftext|>"]
        self.pattern = re.compile(
            r"<\|startoftext\|>|<\|endoftext\|>|"
            r"'s|'t|'re|'ve|'m|'ll|'d| ?[a-z]+| ?[0-9]| ?[^\sA-Za-z0-9]+",
            re.IGNORECASE,
        )

    def encode(self, text: str) -> list[int]:
        text = re.sub(r"\s+", " ", text.strip().lower())
        token_ids = [self.start_id]
        for token in self.pattern.findall(text):
            encoded = "".join(self.byte_encoder[value] for value in token.encode("utf-8"))
            for bpe_token in self._bpe(encoded).split(" "):
                token_ids.append(self.vocab.get(bpe_token, self.end_id))
        token_ids.append(self.end_id)
        if len(token_ids) > self.sequence_length:
            token_ids = token_ids[: self.sequence_length]
            token_ids[-1] = self.end_id
        token_ids.extend([self.end_id] * (self.sequence_length - len(token_ids)))
        return token_ids

    @lru_cache(maxsize=50000)
    def _bpe(self, token: str) -> str:
        if not token:
            return token
        word = tuple(token[:-1]) + (token[-1] + "</w>",)
        pairs = get_pairs(word)
        if not pairs:
            return token + "</w>"

        while True:
            bigram = min(pairs, key=lambda pair: self.bpe_ranks.get(pair, float("inf")))
            if bigram not in self.bpe_ranks:
                break
            first, second = bigram
            new_word: list[str] = []
            index = 0
            while index < len(word):
                try:
                    next_index = word.index(first, index)
                except ValueError:
                    new_word.extend(word[index:])
                    break
                new_word.extend(word[index:next_index])
                index = next_index
                if index < len(word) - 1 and word[index] == first and word[index + 1] == second:
                    new_word.append(first + second)
                    index += 2
                else:
                    new_word.append(word[index])
                    index += 1
            word = tuple(new_word)
            if len(word) == 1:
                break
            pairs = get_pairs(word)
        return " ".join(word)


def get_pairs(word: tuple[str, ...]) -> set[tuple[str, str]]:
    pairs = set()
    previous = word[0]
    for item in word[1:]:
        pairs.add((previous, item))
        previous = item
    return pairs


def bytes_to_unicode() -> dict[int, str]:
    byte_values = list(range(ord("!"), ord("~") + 1))
    byte_values += list(range(ord("¡"), ord("¬") + 1))
    byte_values += list(range(ord("®"), ord("ÿ") + 1))
    chars = byte_values[:]
    n = 0
    for value in range(256):
        if value not in byte_values:
            byte_values.append(value)
            chars.append(256 + n)
            n += 1
    return {value: chr(char) for value, char in zip(byte_values, chars)}
