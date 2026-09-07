"""服务故障与玩家意图分开；日志只记录固定错误码，不记录输入、密钥或 SDK 诊断。"""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from ..config import PROJECT_ROOT, settings

logger = logging.getLogger(__name__)
MESSAGES = {
    'missing_api_key': 'AI 尚未配置，暂时无法理解自由输入。请在项目 .env 中设置 DEEPSEEK_API_KEY；这次没有执行行动。',
    'harness_setup': 'AI 运行环境尚未就绪，暂时无法理解自由输入。请检查 Harness 安装与 Python 版本；这次没有执行行动。',
    'harness_failed': 'AI 服务暂时无法响应，这次没有执行行动。请稍后重试。',
    'ai_unavailable': 'AI 服务暂时无法响应，这次没有执行行动。请稍后重试。',
    'invalid_ai_response': '这次回复未能正常生成，没有执行你的行动。请稍后重试。',
}


def failure_reply(code):
    return MESSAGES.get(code, MESSAGES['ai_unavailable'])


def report_failure(code, error=None):
    code = code if code in MESSAGES else 'ai_unavailable'
    # 不使用 str(error) 或 exc_info：验证异常可能包含原始模型文本。
    logger.warning('AI request failed code=%s backend=%s error_type=%s',
                   code, settings.ai_backend, type(error).__name__ if error else 'configuration')


def configure_cli_log(path=None):
    path = Path(path) if path else PROJECT_ROOT / 'data/logs/cli-ai.log'
    path.parent.mkdir(parents=True, exist_ok=True)
    for handler in logger.handlers[:]:
        if getattr(handler, '_storyworld_cli', False):
            logger.removeHandler(handler)
            handler.close()
    handler = RotatingFileHandler(path, maxBytes=1024 * 1024, backupCount=2, encoding='utf-8')
    handler._storyworld_cli = True
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    # CLI 不把后台故障插进小说；其他入口未配置此 handler 时沿用服务日志。
    logger.propagate = False
    return path


def configuration_issues():
    """只检查本地配置，不调用 API，不改变后端。"""
    issues = []
    if not settings.deepseek_api_key.strip():
        issues.append(('missing_api_key', '项目 .env 或环境变量未配置 DEEPSEEK_API_KEY。'))
    if settings.ai_backend == 'harness':
        from .harness_backend import _load_harness, HarnessError
        try:
            _load_harness()
        except HarnessError as exc:
            issues.append(('harness_setup', str(exc)))  # _load_harness 只含预定义安装提示
    return issues
