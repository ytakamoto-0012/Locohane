import type { IStep } from '@chainlit/react-client';

// Chainlitは @cl.on_chat_start / @cl.on_message のハンドラ本体を暗黙的に
// type: 'run' の合成ステップでラップし、その配下(.steps)に実際の
// assistant_message / tool / llm(思考) ステップをフラットに並べる。
// メインカラム(会話のみ)とサイドパネル(Step群)へ振り分けるため、
// ルート配列を全ノード分再帰的に平坦化してから type で分類する。

/** app.py の TOKEN_USAGE_PREFIX と一致させる（トークン使用量のサイドパネル表示）。 */
export const TOKEN_USAGE_PREFIX = '🔢 トークン使用量\n';
/** app.py の WORK_DIR_PREFIX と一致させる（作業ディレクトリ状態のサイドパネル表示）。 */
export const WORK_DIR_PREFIX = '📁 作業ディレクトリ';
/** src/tools.py の PLAN_PREFIX と一致させる（実行計画のサイドパネル表示）。 */
export const PLAN_PREFIX = '📋 実行計画\n';
/** app.py の STARTER_PREFIX と一致させる（チャット開始時の定型文ボタン表示）。 */
export const STARTER_PREFIX = '🚀 定型文\n';

function flattenAll(nodes: IStep[]): IStep[] {
  const out: IStep[] = [];
  for (const node of nodes) {
    out.push(node);
    if (node.steps?.length) {
      out.push(...flattenAll(node.steps));
    }
  }
  return out;
}

function isPrefixedStatusMessage(step: IStep, prefix: string): boolean {
  return (
    step.type === 'assistant_message' &&
    typeof step.output === 'string' &&
    step.output.startsWith(prefix)
  );
}

export function isTokenUsageMessage(step: IStep): boolean {
  return isPrefixedStatusMessage(step, TOKEN_USAGE_PREFIX);
}

export function isWorkDirMessage(step: IStep): boolean {
  return isPrefixedStatusMessage(step, WORK_DIR_PREFIX);
}

export function isPlanMessage(step: IStep): boolean {
  return isPrefixedStatusMessage(step, PLAN_PREFIX);
}

export function isStarterMessage(step: IStep): boolean {
  return isPrefixedStatusMessage(step, STARTER_PREFIX);
}

/** メインカラムに表示する、ユーザー発言とアシスタントの最終回答のみ。 */
export function selectMainThread(messages: IStep[]): IStep[] {
  return flattenAll(messages).filter(
    (s) =>
      (s.type === 'user_message' || s.type === 'assistant_message') &&
      !isTokenUsageMessage(s) &&
      !isWorkDirMessage(s) &&
      !isPlanMessage(s) &&
      !isStarterMessage(s)
  );
}

function isSideStepType(step: IStep): boolean {
  return step.type !== 'user_message' && step.type !== 'assistant_message' && step.type !== 'run';
}

/**
 * サイドパネルに表示する、ツール呼び出し・思考ブロックなどの実行系Step。
 *
 * 'run'（@cl.on_message ハンドラ本体の合成ラッパー）だけを透過的に展開し、
 * tool/llm 等の実際のStepの親子構造（parent_id）はそのまま維持する。
 * dispatch_agent（サブエージェント実行）配下の内部Stepは、その子として
 * ネストされたまま返るため、StepItem 側で折りたたみ内に再帰表示できる
 * （app.py の _resolve_parent_id が実際の呼び出し階層に基づき parent_id を
 * 設定している）。
 */
export function selectSideSteps(messages: IStep[]): IStep[] {
  const out: IStep[] = [];
  for (const node of messages) {
    if (isSideStepType(node)) {
      out.push(node.steps?.length ? { ...node, steps: selectSideSteps(node.steps) } : node);
    } else if (node.steps?.length) {
      out.push(...selectSideSteps(node.steps));
    }
  }
  return out;
}

/** 直近のトークン使用量メッセージ(cl.Messageとして届く)を1件だけ取り出す。 */
export function selectLatestTokenUsage(messages: IStep[]): IStep | undefined {
  const usageMessages = flattenAll(messages).filter(isTokenUsageMessage);
  return usageMessages[usageMessages.length - 1];
}

/** 直近の作業ディレクトリ状態メッセージ(cl.Messageとして届く)を1件だけ取り出す。 */
export function selectLatestWorkDir(messages: IStep[]): IStep | undefined {
  const workDirMessages = flattenAll(messages).filter(isWorkDirMessage);
  return workDirMessages[workDirMessages.length - 1];
}

/** 直近の実行計画メッセージ(cl.Messageとして届く、update_task_progressで更新される)を1件だけ取り出す。 */
export function selectLatestPlan(messages: IStep[]): IStep | undefined {
  const planMessages = flattenAll(messages).filter(isPlanMessage);
  return planMessages[planMessages.length - 1];
}

/** チャット開始時に届く定型文リスト(cl.Messageとして届く、JSON文字列)を1件だけ取り出しパースする。 */
export function selectLatestStarters(messages: IStep[]): string[] {
  const starterMessages = flattenAll(messages).filter(isStarterMessage);
  const latest = starterMessages[starterMessages.length - 1];
  if (!latest || typeof latest.output !== 'string') return [];
  try {
    const parsed = JSON.parse(latest.output.slice(STARTER_PREFIX.length));
    return Array.isArray(parsed) ? parsed.filter((v): v is string => typeof v === 'string') : [];
  } catch {
    return [];
  }
}
