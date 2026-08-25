/**
 * 统计文本的真实字数（网文平台标准）。
 *
 * 规则：
 * - 每个中文汉字（CJK 统一表意文字）= 1 字
 * - 每个连续英文字母序列 = 1 词
 * - 每个连续数字序列 = 1 词
 * - 标点符号、空格、换行 → 不计
 */
export function countWords(text: string): number {
  if (!text) return 0;
  const chinese = (text.match(/[\u4e00-\u9fff\u3400-\u4dbf]/g) || []).length;
  const english = (text.match(/[a-zA-Z]+/g) || []).length;
  const numbers = (text.match(/\d+/g) || []).length;
  return chinese + english + numbers;
}
