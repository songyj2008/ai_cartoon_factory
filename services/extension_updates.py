"""Reload commands for paired extensions loaded from the application directory."""
import hashlib
import json
import os
import time
import uuid
from pathlib import Path

from services import web_llm_bridge as bridge
from services.context import BASE_DIR


def extension_directory():
    return Path(BASE_DIR) / 'tools' / 'chrome_prompt_bridge'


def source_info():
    directory = extension_directory()
    for name in ('manifest.json', 'worker.js', 'panel.html', 'extension_update.js', 'extension_build.js'):
        if not (directory / name).is_file():
            raise ValueError(f'插件目录缺少 {name}，请检查程序安装包')
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    digest = hashlib.sha256()
    for file in sorted(directory.rglob('*')):
        relative = file.relative_to(directory)
        if ('tests' in relative.parts or file.name == 'extension_build.js' or
                file.suffix not in {'.js', '.json', '.html', '.css', '.png', '.svg'}):
            continue
        digest.update(relative.as_posix().encode('utf-8') + b'\0' + file.read_bytes())
    return {'version': manifest['version'], 'build': digest.hexdigest(), 'path': str(directory.resolve())}


def request_update():
    info = source_info()
    # An imported literal identifies the code loaded before a reload, even when
    # source files on disk already contain the next build.
    destination = extension_directory() / 'extension_build.js'
    temporary = destination.with_name(destination.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text('globalThis.EXTENSION_BUILD_ID = ' + json.dumps(info['build']) + ';\n', encoding='utf-8')
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    browsers = bridge.browser_clients()
    with bridge._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        for browser in browsers:
            existing = db.execute('SELECT value FROM extension_updates WHERE client_id=?', (browser['id'],)).fetchone()
            command = json.loads(existing[0]) if existing else {}
            if command.get('build') != info['build'] or command.get('phase') in {'complete', 'failed'}:
                command = {**info, 'id': uuid.uuid4().hex, 'phase': 'requested'}
                db.execute('INSERT OR REPLACE INTO extension_updates VALUES (?,?)', (browser['id'], json.dumps(command)))
    return status()


def draining(db, client_id):
    row = db.execute('SELECT value FROM extension_updates WHERE client_id=?', (client_id,)).fetchone()
    return bool(row and json.loads(row[0]).get('phase') in {'requested', 'waiting', 'reloading'})


def heartbeat(client_id, browser, receipt):
    with bridge._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT value FROM extension_updates WHERE client_id=?', (client_id,)).fetchone()
        if not row:
            return None
        command = json.loads(row[0])
        if command['phase'] in {'complete', 'failed'}:
            return None
        if not browser.get('update_capable'):
            command.update(phase='failed', message='此版本需要在浏览器中手动重新加载一次，以启用一键更新')
        elif receipt.get('id') == command['id']:
            phase = receipt.get('phase')
            if phase == 'reloaded':
                matched = browser.get('build') == command['build'] and browser.get('version') == command['version']
                command.update(phase='complete' if matched else 'failed',
                               message='已更新' if matched else '重载后代码不匹配，请检查浏览器加载的插件目录或权限提示')
            elif phase in {'waiting', 'reloading', 'failed'}:
                if phase == 'reloading' and command['phase'] != 'reloading':
                    command['reloading_at'] = time.time()
                command['phase'] = phase
                command['message'] = str(receipt.get('message') or '')[:300]
        db.execute('UPDATE extension_updates SET value=? WHERE client_id=?', (json.dumps(command), client_id))
        return command if command['phase'] not in {'complete', 'failed'} else None


def status():
    browsers = bridge.browser_clients()
    with bridge._connect() as db:
        rows = db.execute('SELECT * FROM extension_updates').fetchall()
        commands = {row['client_id']: json.loads(row['value']) for row in rows}
        for client_id, command in commands.items():
            if command['phase'] == 'reloading' and time.time() - command.get('reloading_at', time.time()) > 90:
                command.update(phase='failed', message='重新加载后未收到确认，请检查浏览器权限提示或重新连接后重试')
                db.execute('UPDATE extension_updates SET value=? WHERE client_id=?', (json.dumps(command), client_id))
    return {'source': source_info(), 'browsers': [
        {**browser, 'update': commands.get(browser['id'])} for browser in browsers]}
