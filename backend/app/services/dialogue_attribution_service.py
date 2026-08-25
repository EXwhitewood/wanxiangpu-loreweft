from __future__ import annotations

import re


class DialogueAttributionService:
    DIALOGUE_RE = re.compile(
        r"“(?P<curly>[^”]{1,120})”|\"(?P<straight>[^\"\n]{1,120})\"|「(?P<corner>[^」]{1,120})」"
    )

    def extract_dialogues(self, text: str) -> list[dict]:
        items = []
        for match in self.DIALOGUE_RE.finditer(text or ""):
            quote = next((part for part in match.groups() if part is not None), "").strip()
            before = text[max(0, match.start() - 18):match.start()]
            after = text[match.end():match.end() + 18]
            speaker = self._guess_speaker(before, after)
            items.append({
                "text": quote,
                "speaker": speaker,
                "start": match.start(),
                "end": match.end(),
                "context": (before + match.group(0) + after).replace("\n", " "),
            })
        return items

    @staticmethod
    def _guess_speaker(before: str, after: str) -> str:
        patterns = [
            r"([一-龥A-Za-z0-9·]{1,8})(?:低声|沉声|冷声|笑着|淡淡)?(?:说|问|道|答)",
            r"(?:说|问|道|答)(?:完|罢|着)?，?([一-龥A-Za-z0-9·]{1,8})",
        ]
        for text in (after, before):
            for pattern in patterns:
                m = re.search(pattern, text)
                if m:
                    return m.group(1)
        return ""
