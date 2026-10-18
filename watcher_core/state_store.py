"""Versioned state; preserve old keys and refuse silent resets of corrupt data."""
import json
from pathlib import Path
import shutil
from datetime import datetime

VERSION = 1


class StateError(ValueError):
    pass


def migrate(state):
    if not isinstance(state, dict) or not isinstance(state.get('episodes', {}), dict):
        raise StateError('state.episodes 必须是对象')
    version = state.get('state_version', 0)
    if type(version) is not int or version not in (0, VERSION):
        raise StateError('不支持此状态版本，请使用对应版本程序')
    if type(state.get('initialized', False)) is not bool:
        raise StateError('state.initialized 必须是布尔值')
    for field in ['pending_notifications', 'health', 'deliveries', 'primary_episodes', 'fallback_waiting', 'episode_maps', 'season_ambiguous', 'rule_health']:
        if not isinstance(state.get(field, {}), dict):
            raise StateError(f'state.{field} 必须是对象')
    for message in state.get('pending_notifications', {}).values():
        if not isinstance(message, dict) or any(not isinstance(message.get(k), str)
                                                for k in ['rule_id', 'title', 'body']):
            raise StateError('待发送消息格式错误，请恢复备份')
    for entry in state.get('fallback_waiting', {}).values():
        if not isinstance(entry, dict) or not isinstance(entry.get('signature'), str) or not isinstance(entry.get('candidate'), dict):
            raise StateError('兜底等待记录格式错误，请恢复备份')
        try:
            date = datetime.fromisoformat(entry['first_seen'])
            if date.tzinfo is None:
                raise ValueError('missing timezone')
        except (KeyError, TypeError, ValueError) as error:
            raise StateError('兜底等待时间无效，请恢复备份') from error
    state.setdefault('episodes', {})
    state.setdefault('initialized', False)
    state['state_version'] = VERSION
    state.pop('seen', None)
    return state


def read(path, logger):
    path = Path(path)
    backup = path.with_name('state.backup.json')
    if not path.exists() and not backup.exists():
        return migrate({'episodes': {}, 'initialized': False})
    for candidate in [path, backup]:
        try:
            value = migrate(json.loads(candidate.read_text(encoding='utf-8')))
            if candidate == backup:
                logger.warning('主状态损坏或缺失，使用最近有效备份；原始文件暂不覆盖')
                value['recovered_from_backup'] = True
            return value
        except (OSError, ValueError) as error:
            logger.warning('无法读取状态 %s: %s', candidate.name, type(error).__name__)
    raise StateError('主状态与备份均不可用，已停止执行以避免历史重复推送。请按恢复文档处理。')


def write(path, state):
    migrate(state)
    path = Path(path)
    backup = path.with_name('state.backup.json')
    if path.exists():
        try:
            migrate(json.loads(path.read_text(encoding='utf-8')))
        except (OSError, ValueError):
            # Never overwrite a valid backup with corrupt primary bytes.
            pass
        else:
            shutil.copyfile(path, backup)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def previous_episode(state, rid, episode, group, equivalent):
    episodes = state.get('episodes', {})
    exact = f'{rid}|{episode}|{group}' if group else f'{rid}|{episode}'
    if exact in episodes:
        return episodes[exact]
    for key, value in episodes.items():
        parts = key.split('|', 2)
        if len(parts) == 3 and parts[:2] == [rid, episode] and equivalent(parts[2], group):
            return value
    # Old two-part baselines were shared across groups; preserve them on upgrade.
    return episodes.get(f'{rid}|{episode}')
