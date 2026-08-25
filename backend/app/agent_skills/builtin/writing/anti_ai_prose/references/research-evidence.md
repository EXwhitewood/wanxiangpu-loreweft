# 研究依据与采用边界

## 采用的结论

- 生成文本可能表现出较高可预测性和较低节奏变化，但这些只能作为代理信号。
- 多信号组合优于单一困惑度或 detector 分数。
- 人工修订、新模型和文体差异会显著削弱统计检测能力。
- 非母语写作者容易被检测器误判，因此本 skill 不输出作者身份结论。
- 成熟 humanizer 项目普遍采用 detect -> rewrite -> recheck 闭环，并强调 voice/personality。

## 不采用的做法

- 不以绕过 Turnitin、GPTZero 或其他 detector 为目标。
- 不人为追求高困惑度。
- 不添加错别字、病句、随机断裂、虚假经历。
- 不把单个词、破折号或句长当作 AI 身份证据。
- 不允许为“人味”改变事实、极性、因果、责任或角色意图。

## 主要来源

- Wikipedia Signs of AI writing  
  https://en.wikipedia.org/wiki/Wikipedia:Signs_of_AI_writing
- GLTR  
  https://arxiv.org/abs/1906.04043
- DetectGPT  
  https://arxiv.org/abs/2301.11305
- OpenAI classifier retirement  
  https://openai.com/index/new-ai-classifier-for-indicating-ai-written-text/
- Stanford HAI detector bias  
  https://hai.stanford.edu/news/ai-detectors-biased-against-non-native-english-writers
- blader/humanizer  
  https://github.com/blader/humanizer
- avoid-ai-writing  
  https://github.com/conorbronsdon/avoid-ai-writing
- Patina  
  https://github.com/devswha/patina
