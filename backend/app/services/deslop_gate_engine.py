"""去AI味 Gate 化修复引擎。

Gate 化策略层，让 checker 和 repair agent 使用同一套分级。
关键原则：
1. 去 AI 味只改"怎么说"，不改"说什么"
2. 受保护跨度包括伏笔、钩子、人物状态、事实命题、结尾合同
3. 删除比例必须有上限
4. 项目可配置白名单，避免专有名词误报
5. 修复后必须复检事实和风格

Gate 分级：
  A = 禁用词/高频AI词
  B = 句式套路
  C = 心理告知/解释腔
  D = 段落节奏均匀
  E = 对话标签与人物声音同质
  F = 章末总结/升华腔
  G = 夸大意义/宣传性语言
  H = 填充短语/模糊归因
  I = 明喻过度
  J = 通感公式
  K = 虚假代理
  L = 模板承接（记忆回溯类）
  M = 重复句
"""
from __future__ import annotations
import logging
import re
from typing import Optional

from app.models.deslop_gate import (
    DeslopGateReport,
    DeslopRepairPlan,
    DeslopResult,
    GATE_DESCRIPTIONS,
)
from app.utils.dash_artifacts import DASH_ARTIFACT_RE

logger = logging.getLogger(__name__)


class DeslopGateEngine:
    """去AI味 Gate 化修复引擎"""

    # Gate A: 禁用词/高频AI词
    AI_FLAVOR_WORDS: set[str] = {
        "不禁", "竟然", "居然", "恍然", "蓦然", "陡然",
        "心中暗道", "暗自思忖", "不由得", "情不自禁",
        "深吸一口气", "眼中闪过一丝", "嘴角勾起", "心猛地一沉",
        "某种", "复杂的情绪", "眼神复杂", "命运的齿轮",
        "宛如", "犹如", "仿佛", "恰似",
        "令人", "让人", "使人",
        "无与伦比", "前所未有", "不可思议",
        "震撼", "惊艳", "绝美",
        # ---- 夸大象征意义 ----
        "标志着", "见证了", "象征着", "彰显了", "凸显了",
        "体现了", "证明了", "昭示着", "预示着", "折射出",
        "反映了", "揭示了",
        # ---- 系动词回避 ----
        "充当", "扮演着", "构成了", "拥有着", "设有",
        # ---- AI 高频情绪短语 ----
        "心中涌起", "一股暖流", "一阵酸楚",
        "五味杂陈", "百感交集", "心潮澎湃",
        "内心波澜", "情绪激荡", "莫名的感动",
        "说不出的滋味", "复杂的心情",
    }

    # Gate B: 句式套路
    CLICHE_PATTERNS: list[re.Pattern] = [
        re.compile(r"不是.{1,20}?[，,、\s]*而是.{1,20}"),
        re.compile(r"既.{1,5}又.{1,5}"),
        re.compile(r"不仅.{1,20}?[，,、\s]*而且.{1,20}"),
        re.compile(r"虽然.{1,20}?[，,、\s]*但是.{1,20}"),
        re.compile(rf"(?:{DASH_ARTIFACT_RE.pattern}).{{1,20}}(?:{DASH_ARTIFACT_RE.pattern})"),
        # ---- 否定式排比 ----
        re.compile(r"不是[^。！？]{1,20}[，,、\s]*不是[^。！？]{1,20}[，,、\s]*而是[^。！？]{1,30}"),
        re.compile(r"这不仅仅是[^。！？]{1,20}[，,、\s]*更是[^。！？]{1,30}"),
        re.compile(r"不仅没有[^。！？]{1,20}[，,、\s]*反而[^。！？]{1,30}"),
        re.compile(r"与其说[^。！？]{1,20}[，,、\s]*不如说[^。！？]{1,30}"),
        # ---- 虚假范围 ----
        re.compile(r"从[^。！？]{2,15}到[^。！？]{2,15}[,，]\s*从[^。！？]{2,15}到[^。！？]{2,15}"),
        re.compile(r"无论是[^。！？]{2,15}还是[^。！？]{2,15}[，,]\s*都[^。！？]{2,20}"),
    ]

    # Gate C: 心理告知/解释腔
    EXPLANATION_PATTERNS: list[re.Pattern] = [
        re.compile(r"(他|她|它)(想|觉得|认为|知道|明白|意识到).{0,5}(是因为|是由于|原来)"),
        re.compile(r"(心中|心里|内心)(暗想|想着|想着想着)"),
        re.compile(r"(其实|实际上|事实上).{0,10}(就是|不过是)"),
        # ---- 解释腔扩展 ----
        re.compile(r"[他她其][^。！？]{0,8}(?:终于明白|终于理解|终于意识到|终于懂了)"),
        re.compile(r"[他她其][^。！？]{0,8}(?:心中暗想|暗自思忖|暗暗下定决心)"),
        re.compile(r"[他她其][^。！？]{0,8}(?:不禁想|不由得想|忍不住想)"),
    ]

    # Gate F: 章末总结/升华腔
    ENDING_SUMMARY_PATTERNS: list[re.Pattern] = [
        re.compile(r"(这一刻|此时此刻|就这样).{0,20}(终于|总算|终究)"),
        re.compile(r"(或许|也许|大概).{0,15}(才是|就是).{0,10}(真谛|意义|答案)"),
        re.compile(r"(回望|回想|回首).{0,20}(一切都|所有都)"),
        # ---- 通用积极结论 ----
        re.compile(r"(未来可期|光明就在前方|一切都会好起来|新的篇章即将开启)"),
        re.compile(r"(最好的还在后头|这只是一个开始|一切终将归于美好)"),
        re.compile(r"(希望就在不远处|崭新的一页|迈向更好的明天)"),
    ]

    # Gate G: 夸大意义/宣传性语言
    INFLATED_SIGNIFICANCE_WORDS: set[str] = {
        "至关重要", "举足轻重", "不可或缺", "不可磨灭",
        "划时代", "里程碑", "转折点", "分水岭",
        "历史性", "开创性", "革命性", "颠覆性", "突破性",
        "深远影响", "持久影响",
        "永恒的", "不朽的", "深深植根",
        "奠定基础", "铺平道路", "开启新纪元",
        "塑造了", "改变了格局", "推动了进程",
    }

    PROMOTIONAL_WORDS: set[str] = {
        "令人叹为观止", "叹为观止", "令人叹服",
        "令人惊叹", "令人震撼", "令人瞩目", "令人钦佩",
        "令人向往", "令人心驰神往",
        "美轮美奂", "巧夺天工", "鬼斧神工",
        "独一无二", "绝无仅有", "举世无双",
        "享誉世界", "闻名遐迩", "蜚声中外",
        "坐落于", "充满活力", "充满魅力", "充满生机",
        "丰富多彩", "博大精深", "源远流长",
        "人杰地灵", "钟灵毓秀", "物华天宝",
        "必游之地", "不可错过",
    }

    # Gate H: 填充短语/模糊归因
    FILLER_PHRASES: set[str] = {
        "为了实现这一目标", "在这一过程中",
        "在某种意义上", "从某种程度上说", "在一定程度上",
        "在当前背景下", "关于这一点", "考虑到这一点",
        "基于以上分析", "通过以上分析",
        "经过深入思考", "经过深思熟虑",
        "不言自明", "理所当然", "顺理成章",
    }

    VAGUE_ATTRIBUTION_PATTERNS: list[re.Pattern] = [
        re.compile(r"(?:有人说|有人认为|有人觉得|有人指出)[^。！？]{2,40}"),
        re.compile(r"(?:众所周知|众所周知地)[^。！？]{2,30}"),
        re.compile(r"(?:不言而喻|毋庸置疑)[^。！？]{2,30}"),
        re.compile(r"(?:行业报告显示|观察者指出|专家认为|专家指出)[^。！？]{2,40}"),
        re.compile(r"(?:多个来源|多个出版物|多项研究)[^。！？]{2,30}"),
    ]

    # Gate I: 明喻过度
    SIMILE_PATTERNS: list[re.Pattern] = [
        # 完整明喻结构（带结尾词）
        re.compile(r"像[^。！？\n]{1,30}一样"),
        re.compile(r"像[^。！？\n]{1,30}似的"),
        re.compile(r"像[^。！？\n]{1,30}般"),
        re.compile(r"像[^。！？\n]{1,30}一般"),
        re.compile(r"好像[^。！？\n]{1,30}一样"),
        re.compile(r"好像[^。！？\n]{1,30}似的"),
        re.compile(r"仿佛[^。！？\n]{1,30}"),
        re.compile(r"如同[^。！？\n]{1,30}"),
        re.compile(r"宛如[^。！？\n]{1,30}"),
        re.compile(r"犹如[^。！？\n]{1,30}"),
        re.compile(r"恰似[^。！？\n]{1,30}"),
        # 直接明喻（"像" + 名词短语，2-20字，排除"像是"表推测）
        re.compile(r"像[^像是。！？\n，,]{2,20}"),
        re.compile(r"好像[^像是。！？\n，,]{2,20}"),
    ]
    SIMILE_WORD: str = "像"

    # Gate J: 通感公式
    SYNESTHESIA_PATTERNS: list[re.Pattern] = [
        re.compile(r"[^。！？\n]{1,10}中带着[^。！？\n]{1,10}"),
        re.compile(r"[^。！？\n]{1,10}中透着[^。！？\n]{1,10}"),
        re.compile(r"[^。！？\n]{1,10}里掺着[^。！？\n]{1,10}"),
        re.compile(r"[^。！？\n]{1,10}中含着[^。！？\n]{1,10}"),
        re.compile(r"[^。！？\n]{1,10}里带着[^。！？\n]{1,10}"),
    ]

    # Gate K: 虚假代理
    FALSE_AGENCY_PATTERNS: list[re.Pattern] = [
        re.compile(r"决定出现了"),
        re.compile(r"决定产生了"),
        re.compile(r"变化发生了"),
        re.compile(r"改变出现了"),
        re.compile(r"想法涌上来了"),
        re.compile(r"念头浮现了"),
        re.compile(r"思绪涌上心头"),
        re.compile(r"主意冒了出来"),
    ]

    # Gate L: 模板承接（记忆回溯类）
    TEMPLATE_CARRYOVER_PHRASES: set[str] = {
        "前世记忆告诉她",
        "记忆里有一句话",
        "她记得",
        "他记得",
        "脑海中浮现",
        "记忆深处",
        "前世的记忆涌上心头",
        "记忆中有一道声音",
    }

    def __init__(self):
        self._whitelist: set[str] = set()

    def set_whitelist(self, words: list[str]):
        """设置项目白名单"""
        self._whitelist = set(words)

    # ---- 所有合法 Gate 列表 ----
    ALL_GATES = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M"]

    # Gate M: 重复句检测的最小句子长度（短句可能是合法修辞重复）
    DUPLICATE_MIN_SENTENCE_LEN: int = 12
    # Gate M: 重复句检测的最大重复次数容忍（超过才报）
    DUPLICATE_TOLERANCE: int = 1

    # Gate I 修复：模糊归因明喻（最该删的"像有什么东西/像被人"句式）
    # 这些是 AI 写作的典型模糊归因，删除"像…包装"保留核心动作即可
    VAGUE_AGENCY_SIMILE_RE: re.Pattern = re.compile(
        r"像(?:被|有什么|有人|什么东西|有东西|有谁|被人|是谁|是被)"
        r"[^。！？\n]{2,40}?(?:一样|似的|般|一般|了|着|过|地|的|来|去|住|起|开|下|上)"
    )
    # Gate I 修复：现代器物比喻替换词表（仙侠题材为主）
    # 把现代器物/概念替换为题材贴合的等价物，避免出戏
    MODERN_METAPHOR_REPLACEMENTS: dict[str, str] = {
        "阀门": "闸口",
        "开关": "机括",
        "机器": "机关",
        "齿轮": "机牙",
        "屏幕": "光幕",
        "信号": "气机",
        "电量": "灵力",
        "电流": "灵气",
        "按钮": "阵眼",
        "陶罐": "竹筒",
        "玻璃": "琉璃",
        "塑料": "油布",
        "水泥": "石砖",
        "铁门": "铜门",
        "钢刀": "精铁刀",
        "手机": "传音符",
        "电脑": "阵盘",
        "网络": "灵网",
        "数据": "消息",
        "程序": "阵法",
        "系统": "阵盘",
        "芯片": "阵心",
    }

    def detect(self, text: str, gates: list[str] | None = None) -> list[DeslopGateReport]:
        """检测文本中的AI味问题

        Args:
            text: 待检测文本
            gates: 要检测的Gate列表，None表示全部

        Returns:
            Gate检测报告列表
        """
        if gates is None:
            gates = self.ALL_GATES

        reports = []

        if "A" in gates:
            reports.append(self._detect_gate_a(text))
        if "B" in gates:
            reports.append(self._detect_gate_b(text))
        if "C" in gates:
            reports.append(self._detect_gate_c(text))
        if "D" in gates:
            reports.append(self._detect_gate_d(text))
        if "E" in gates:
            reports.append(self._detect_gate_e(text))
        if "F" in gates:
            reports.append(self._detect_gate_f(text))
        if "G" in gates:
            reports.append(self._detect_gate_g(text))
        if "H" in gates:
            reports.append(self._detect_gate_h(text))
        if "I" in gates:
            reports.append(self._detect_gate_i(text))
        if "J" in gates:
            reports.append(self._detect_gate_j(text))
        if "K" in gates:
            reports.append(self._detect_gate_k(text))
        if "L" in gates:
            reports.append(self._detect_gate_l(text))
        if "M" in gates:
            reports.append(self._detect_gate_m(text))

        return [r for r in reports if r.evidence_spans]

    def _detect_gate_a(self, text: str) -> DeslopGateReport:
        """Gate A: 禁用词/高频AI词"""
        evidence = []
        whitelist_hits = []
        for word in self.AI_FLAVOR_WORDS:
            if word in text:
                if word in self._whitelist:
                    whitelist_hits.append(word)
                else:
                    # 提取上下文
                    for m in re.finditer(re.escape(word), text):
                        start = max(0, m.start() - 10)
                        end = min(len(text), m.end() + 10)
                        evidence.append(text[start:end])

        return DeslopGateReport(
            gate="A",
            severity="medium" if len(evidence) <= 3 else "high",
            evidence_spans=evidence[:10],
            repair_scope="word",
            deletion_limit=0.1,
            whitelist_hits=whitelist_hits,
        )

    def _detect_gate_b(self, text: str) -> DeslopGateReport:
        """Gate B: 句式套路"""
        evidence = []
        for pattern in self.CLICHE_PATTERNS:
            for m in pattern.finditer(text):
                evidence.append(m.group())

        return DeslopGateReport(
            gate="B",
            severity="low" if len(evidence) <= 2 else "medium",
            evidence_spans=evidence[:10],
            repair_scope="phrase",
            deletion_limit=0.2,
        )

    def _detect_gate_c(self, text: str) -> DeslopGateReport:
        """Gate C: 心理告知/解释腔"""
        evidence = []
        for pattern in self.EXPLANATION_PATTERNS:
            for m in pattern.finditer(text):
                evidence.append(m.group())

        return DeslopGateReport(
            gate="C",
            severity="medium" if evidence else "low",
            evidence_spans=evidence[:10],
            repair_scope="sentence",
            deletion_limit=0.3,
        )

    def _detect_gate_d(self, text: str) -> DeslopGateReport:
        """Gate D: 段落节奏均匀"""
        paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
        if not paragraphs:
            return DeslopGateReport(gate="D", severity="low", evidence_spans=[], repair_scope="paragraph", deletion_limit=0.2)

        # 检查段落长度是否过于均匀
        lengths = [len(p) for p in paragraphs]
        if len(lengths) >= 3:
            avg = sum(lengths) / len(lengths)
            variance = sum((l - avg) ** 2 for l in lengths) / len(lengths)
            std_dev = variance ** 0.5
            # 如果标准差很小，说明段落长度过于均匀
            if std_dev < avg * 0.2 and avg > 50:
                return DeslopGateReport(
                    gate="D",
                    severity="low",
                    evidence_spans=[f"段落长度标准差({std_dev:.0f})过小，平均长度({avg:.0f})"],
                    repair_scope="paragraph",
                    deletion_limit=0.2,
                )

        return DeslopGateReport(gate="D", severity="low", evidence_spans=[], repair_scope="paragraph", deletion_limit=0.2)

    def _detect_gate_e(self, text: str) -> DeslopGateReport:
        """Gate E: 对话标签与人物声音同质"""
        # 简单检测：统计对话标签的多样性
        dialogue_tags = re.findall(r"(他|她|它|我|你)(说|道|问|答|喊|叫|笑|叹)", text)
        if not dialogue_tags:
            return DeslopGateReport(gate="E", severity="low", evidence_spans=[], repair_scope="phrase", deletion_limit=0.2)

        unique_tags = set(dialogue_tags)
        if len(dialogue_tags) > 5 and len(unique_tags) <= 2:
            return DeslopGateReport(
                gate="E",
                severity="medium",
                evidence_spans=[f"对话标签过于单一: {dict((tag, dialogue_tags.count(tag)) for tag in unique_tags)}"],
                repair_scope="phrase",
                deletion_limit=0.2,
            )

        return DeslopGateReport(gate="E", severity="low", evidence_spans=[], repair_scope="phrase", deletion_limit=0.2)

    def _detect_gate_f(self, text: str) -> DeslopGateReport:
        """Gate F: 章末总结/升华腔"""
        evidence = []
        # 只检查最后3段
        paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
        ending_text = "\n".join(paragraphs[-3:]) if len(paragraphs) >= 3 else text

        for pattern in self.ENDING_SUMMARY_PATTERNS:
            for m in pattern.finditer(ending_text):
                evidence.append(m.group())

        return DeslopGateReport(
            gate="F",
            severity="high" if evidence else "low",
            evidence_spans=evidence[:5],
            repair_scope="sentence",
            deletion_limit=0.3,
        )

    def _detect_gate_g(self, text: str) -> DeslopGateReport:
        """Gate G: 夸大意义/宣传性语言"""
        evidence = []
        whitelist_hits = []

        for word in self.INFLATED_SIGNIFICANCE_WORDS | self.PROMOTIONAL_WORDS:
            if word in text:
                if word in self._whitelist:
                    whitelist_hits.append(word)
                else:
                    for m in re.finditer(re.escape(word), text):
                        start = max(0, m.start() - 10)
                        end = min(len(text), m.end() + 10)
                        evidence.append(text[start:end])

        return DeslopGateReport(
            gate="G",
            severity="medium" if len(evidence) <= 3 else "high",
            evidence_spans=evidence[:10],
            repair_scope="word",
            deletion_limit=0.15,
            whitelist_hits=whitelist_hits,
        )

    def _detect_gate_h(self, text: str) -> DeslopGateReport:
        """Gate H: 填充短语/模糊归因"""
        evidence = []
        whitelist_hits = []

        # 填充短语
        for phrase in self.FILLER_PHRASES:
            if phrase in text:
                if phrase in self._whitelist:
                    whitelist_hits.append(phrase)
                else:
                    pos = text.find(phrase)
                    start = max(0, pos - 10)
                    end = min(len(text), pos + len(phrase) + 10)
                    evidence.append(text[start:end])

        # 模糊归因模式
        for pattern in self.VAGUE_ATTRIBUTION_PATTERNS:
            for m in pattern.finditer(text):
                evidence.append(m.group())

        return DeslopGateReport(
            gate="H",
            severity="low" if len(evidence) <= 2 else "medium",
            evidence_spans=evidence[:10],
            repair_scope="phrase",
            deletion_limit=0.2,
            whitelist_hits=whitelist_hits,
        )

    def _detect_gate_i(self, text: str) -> DeslopGateReport:
        """Gate I: 明喻过度（像...一样/似的/般 + '像'字独立密度双重检测）"""
        evidence = []
        for pattern in self.SIMILE_PATTERNS:
            for m in pattern.finditer(text):
                evidence.append(m.group())

        text_len = max(len(text), 1)
        density = len(evidence) / max(text_len / 1000, 1)
        word_count = text.count(self.SIMILE_WORD)
        word_density = word_count / max(text_len / 1000, 1)

        # 双重触发：完整结构密度 >= 1.5/千字，或'像'字密度 >= 3.0/千字
        if density < 1.5 and word_density < 3.0:
            return DeslopGateReport(gate="I", severity="low", evidence_spans=[], repair_scope="phrase", deletion_limit=0.15)

        if density >= 3.0 or word_density >= 5.0:
            severity = "high"
        else:
            severity = "medium"
        return DeslopGateReport(
            gate="I",
            severity=severity,
            evidence_spans=evidence[:10],
            repair_scope="phrase",
            deletion_limit=0.15,
        )

    def _detect_gate_j(self, text: str) -> DeslopGateReport:
        """Gate J: 通感公式（X中带着Y/X中透着Y/X里掺着Y）"""
        evidence = []
        for pattern in self.SYNESTHESIA_PATTERNS:
            for m in pattern.finditer(text):
                evidence.append(m.group())

        if len(evidence) <= 2:
            return DeslopGateReport(gate="J", severity="low", evidence_spans=[], repair_scope="phrase", deletion_limit=0.15)

        severity = "high" if len(evidence) >= 4 else "medium"
        return DeslopGateReport(
            gate="J",
            severity=severity,
            evidence_spans=evidence[:10],
            repair_scope="phrase",
            deletion_limit=0.15,
        )

    def _detect_gate_k(self, text: str) -> DeslopGateReport:
        """Gate K: 虚假代理（决定出现了/改变发生了/想法涌上来了）"""
        evidence = []
        for pattern in self.FALSE_AGENCY_PATTERNS:
            for m in pattern.finditer(text):
                start = max(0, m.start() - 10)
                end = min(len(text), m.end() + 10)
                evidence.append(text[start:end])

        if not evidence:
            return DeslopGateReport(gate="K", severity="low", evidence_spans=[], repair_scope="sentence", deletion_limit=0.2)

        severity = "high" if len(evidence) >= 3 else "medium"
        return DeslopGateReport(
            gate="K",
            severity=severity,
            evidence_spans=evidence[:10],
            repair_scope="sentence",
            deletion_limit=0.2,
        )

    def _detect_gate_l(self, text: str) -> DeslopGateReport:
        """Gate L: 模板承接（记忆回溯类：前世记忆告诉她/记忆里有一句话/脑海中浮现）"""
        evidence = []
        whitelist_hits = []
        for phrase in self.TEMPLATE_CARRYOVER_PHRASES:
            if phrase in text:
                if phrase in self._whitelist:
                    whitelist_hits.append(phrase)
                else:
                    for m in re.finditer(re.escape(phrase), text):
                        start = max(0, m.start() - 10)
                        end = min(len(text), m.end() + 10)
                        evidence.append(text[start:end])

        if not evidence:
            return DeslopGateReport(gate="L", severity="low", evidence_spans=[], repair_scope="phrase", deletion_limit=0.15)

        severity = "high" if len(evidence) >= 4 else "medium"
        return DeslopGateReport(
            gate="L",
            severity=severity,
            evidence_spans=evidence[:10],
            repair_scope="phrase",
            deletion_limit=0.15,
            whitelist_hits=whitelist_hits,
        )

    def _detect_gate_m(self, text: str) -> DeslopGateReport:
        """Gate M: 重复句（完全相同或高度相似的句子重复出现）

        检测正文里出现的整句重复（如 writer 生成时偶尔会复制粘贴上一句）。
        只检测长度 >= DUPLICATE_MIN_SENTENCE_LEN 的句子，避免合法修辞重复误报。
        """
        if not text or len(text) < self.DUPLICATE_MIN_SENTENCE_LEN:
            return DeslopGateReport(gate="M", severity="low", evidence_spans=[], repair_scope="sentence", deletion_limit=0.1)

        # 按句号/问号/感叹号切句，保留句子内容（去首尾空白）
        sentences = re.split(r"[。！？\n]", text)
        sentences = [s.strip() for s in sentences if s and len(s.strip()) >= self.DUPLICATE_MIN_SENTENCE_LEN]

        # 统计每个句子出现次数
        from collections import Counter
        counter = Counter(sentences)

        evidence: list[str] = []
        for sentence, count in counter.items():
            if count > self.DUPLICATE_TOLERANCE:
                # 重复句作为证据（保留句子本身，便于修复时定位）
                evidence.append(sentence)

        if not evidence:
            return DeslopGateReport(gate="M", severity="low", evidence_spans=[], repair_scope="sentence", deletion_limit=0.1)

        severity = "high" if len(evidence) >= 3 else "medium"
        return DeslopGateReport(
            gate="M",
            severity=severity,
            evidence_spans=evidence[:10],
            repair_scope="sentence",
            deletion_limit=0.1,
        )

    def create_repair_plan(
        self,
        reports: list[DeslopGateReport],
        max_delete_ratio: float = 0.3,
        preserve_plot: bool = True,
    ) -> DeslopRepairPlan:
        """根据检测报告创建修复计划"""
        gates_to_apply = [r.gate for r in reports if r.evidence_spans]
        return DeslopRepairPlan(
            gates_to_apply=gates_to_apply,
            max_delete_ratio=max_delete_ratio,
            preserve_plot_functions=preserve_plot,
            patch_strategy="rephrase",
        )

    def apply_repair(
        self,
        text: str,
        plan: DeslopRepairPlan,
        reports: list[DeslopGateReport],
    ) -> DeslopResult:
        """应用修复计划（确定性部分）

        Gate A/G/H: 删除或替换高频AI词/夸大词/填充短语
        Gate B: 标记句式套路（需LLM重写）
        Gate C: 标记解释腔（需LLM重写）
        Gate D/E/F: 仅检测，由LLM修复
        Gate I: 明喻削減（模糊归因明喻删包装 + 现代器物比喻替换）
        Gate J: 通感公式删减（删"X中带着Y"结构）
        Gate K: 虚假代理删除（删"决定出现了"等无主语句）
        Gate L: 仅检测，由LLM修复（模板承接）
        Gate M: 重复句去重（只保留第一次出现）
        """
        repaired = text
        total_issues = sum(len(r.evidence_spans) for r in reports)
        fixed = 0

        for gate in plan.gates_to_apply:
            report = next((r for r in reports if r.gate == gate), None)
            if not report or not report.evidence_spans:
                continue

            if gate == "A":
                # Gate A: 替换高频AI词
                for word in self.AI_FLAVOR_WORDS:
                    if word in repaired and word not in self._whitelist:
                        repaired = repaired.replace(word, "")
                        fixed += 1

            elif gate == "G":
                # Gate G: 删除夸大意义/宣传性语言
                for word in self.INFLATED_SIGNIFICANCE_WORDS | self.PROMOTIONAL_WORDS:
                    if word in repaired and word not in self._whitelist:
                        repaired = repaired.replace(word, "")
                        fixed += 1

            elif gate == "H":
                # Gate H: 删除填充短语
                for phrase in self.FILLER_PHRASES:
                    if phrase in repaired and phrase not in self._whitelist:
                        repaired = repaired.replace(phrase, "")
                        fixed += 1

            elif gate == "I":
                # Gate I: 明喻削減（确定性）
                # 1. 模糊归因明喻：删"像被什么东西/像有人"包装，保留核心动作
                #    如「身体像被什么东西从后面猛推了一把」→「身体被从后面猛推」
                for m in self.VAGUE_AGENCY_SIMILE_RE.finditer(repaired):
                    matched = m.group()
                    # 提取核心动作：删除"像"和"被/有什么东西/有人"前缀
                    core = re.sub(r"^像(?:被|有什么|有人|什么东西|有东西|有谁|被人|是谁|是被)\s*", "", matched)
                    # 删除尾部"一样/似的/般/一般"
                    core = re.sub(r"(?:一样|似的|般|一般)$", "", core)
                    # 核心动作如果非空且有实际内容，用核心替换明喻；否则直接删明喻
                    if len(core) >= 4:
                        repaired = repaired.replace(matched, core, 1)
                    else:
                        repaired = repaired.replace(matched, "", 1)
                    fixed += 1
                # 2. 现代器物比喻替换（题材贴合）
                for modern, genre_fit in self.MODERN_METAPHOR_REPLACEMENTS.items():
                    if modern in repaired and modern not in self._whitelist:
                        repaired = repaired.replace(modern, genre_fit)
                        fixed += 1

            elif gate == "J":
                # Gate J: 通感公式删减（确定性）
                # 删除"X中带着Y/X中透着Y"结构，保留主感官
                for pattern in self.SYNESTHESIA_PATTERNS:
                    for m in pattern.finditer(repaired):
                        matched = m.group()
                        # 提取主感官（"中带着/中透着"之前的部分）
                        core = re.split(r"中(?:带着|透着)|里(?:掺着|带着)", matched)[0]
                        if len(core) >= 4:
                            repaired = repaired.replace(matched, core, 1)
                            fixed += 1

            elif gate == "K":
                # Gate K: 虚假代理删除（确定性）
                # 删除"决定出现了/改变发生了/想法涌上来了"等无主语虚句
                for pattern in self.FALSE_AGENCY_PATTERNS:
                    for m in pattern.finditer(repaired):
                        matched = m.group()
                        # 虚假代理句直接删除（前后可能的多余标点留给后续清理）
                        repaired = repaired.replace(matched, "", 1)
                        fixed += 1

            elif gate == "M":
                # Gate M: 重复句去重（确定性）
                # 完全相同的句子只保留第一次出现
                for evidence in report.evidence_spans:
                    # 找到所有出现位置，保留第一次，删除后续
                    occurrences = [i for i in range(len(repaired)) if repaired.startswith(evidence, i)]
                    if len(occurrences) > 1:
                        # 从后往前删，避免索引偏移
                        for idx in reversed(occurrences[1:]):
                            repaired = repaired[:idx] + repaired[idx + len(evidence):]
                            fixed += 1

        # 清理删除后可能留下的多余空格/标点
        repaired = re.sub(r"[，,]\s*[，,]", "，", repaired)
        repaired = re.sub(r"[。]\s*[。]", "。", repaired)
        repaired = re.sub(r"\s{2,}", " ", repaired)

        # 计算删除比例
        original_len = len(text)
        repaired_len = len(repaired)
        deletion_ratio = (original_len - repaired_len) / original_len if original_len > 0 else 0.0

        # 如果删除比例超过限制，回退
        if deletion_ratio > plan.max_delete_ratio:
            logger.warning(f"删除比例({deletion_ratio:.1%})超过上限({plan.max_delete_ratio:.1%})，回退部分修复")
            repaired = text
            deletion_ratio = 0.0

        return DeslopResult(
            gate_reports=reports,
            repair_plan=plan,
            original_text=text,
            repaired_text=repaired,
            total_issues=total_issues,
            fixed_issues=fixed,
            deletion_ratio=deletion_ratio,
        )


# 全局单例
_deslop_engine: DeslopGateEngine | None = None

def get_deslop_gate_engine() -> DeslopGateEngine:
    global _deslop_engine
    if _deslop_engine is None:
        _deslop_engine = DeslopGateEngine()
    return _deslop_engine
