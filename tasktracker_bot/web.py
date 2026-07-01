"""
Task Tracker - Web Interface
"""
import os
import sys
import secrets
import urllib.parse
from datetime import datetime
from functools import wraps

import httpx
from flask import Flask, render_template_string, jsonify, request, redirect, url_for, session
from werkzeug.middleware.proxy_fix import ProxyFix

sys.path.insert(0, os.path.dirname(__file__))
from config import (
    WEB_PORT,
    TASKTRACKER_SECRET_KEY,
    TASKTRACKER_ALLOWED_EMAILS,
    TASKTRACKER_GOOGLE_CLIENT_ID,
    TASKTRACKER_GOOGLE_CLIENT_SECRET,
    TASKTRACKER_GOOGLE_REDIRECT_URI,
)
from db import (
    get_all_tasks, get_task_by_id, update_task_status, delete_task, update_task,
    bulk_update_status, bulk_delete, get_all_tags, set_task_tags, cleanup_unused_tags,
    TaskStatus, TaskPriority
)

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.secret_key = TASKTRACKER_SECRET_KEY or os.urandom(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_SAMESITE="Lax",
)


STATUS_ICONS = {
    "new": "🆕",
    "in_progress": "▶️",
    "done": "✅",
    "cancelled": "❌"
}

LOGIN_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Task Tracker • Вход</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f5f5; padding: 20px; }
        .container { max-width: 520px; margin: 0 auto; }
        .card { background: white; border-radius: 12px; padding: 24px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }
        h1 { font-size: 20px; margin-bottom: 8px; color: #333; }
        p { color: #666; margin-bottom: 16px; line-height: 1.4; }
        .btn { display: inline-block; background: #4285F4; color: white; padding: 12px 14px; border-radius: 8px; text-decoration: none; font-weight: 600; }
        .error { background: #fff3f3; border: 1px solid #ffd0d0; color: #b00020; padding: 12px; border-radius: 8px; margin-bottom: 16px; }
        .meta { margin-top: 14px; font-size: 12px; color: #888; }
    </style>
</head>
<body>
    <div class="container">
        <div class="card">
            <h1>📋 Task Tracker</h1>
            <p>Вход через Google нужен, чтобы скрыть задачи от посторонних.</p>
            {% if error %}
            <div class="error">{{ error }}</div>
            {% endif %}
            <a class="btn" href="/auth/google?next={{ next_url | urlencode }}">Войти через Google</a>
        </div>
    </div>
</body>
</html>
"""


LIST_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="icon" type="image/x-icon" href="{{ url_for('static', filename='favicon.ico') }}">
    <link rel="icon" type="image/png" href="{{ url_for('static', filename='favicon.png') }}">
    <title>Task Tracker</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f5f5; padding: 20px; }
        .container { max-width: 1200px; margin: 0 auto; }
        .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; flex-wrap: wrap; gap: 15px; }
        h1 a { text-decoration: none; color: #333; }
        .tabs { display: flex; gap: 5px; }
        .tab-btn { padding: 8px 16px; border: none; border-radius: 4px; cursor: pointer; background: #e0e0e0; font-size: 14px; text-decoration: none; color: #333; }
        .tab-btn.active { background: #4CAF50; color: white; }
        .tab-count { background: rgba(0,0,0,0.1); padding: 2px 6px; border-radius: 10px; font-size: 12px; margin-left: 5px; }
        .tab-btn.active .tab-count { background: rgba(255,255,255,0.2); }
        .task { background: white; border-radius: 8px; padding: 15px; margin-bottom: 10px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); position: relative; }
        .task.selected { border: 2px solid #4CAF50; }
        .task-row { display: flex; align-items: center; gap: 10px; cursor: pointer; }
        .task-checkbox { width: 18px; height: 18px; cursor: pointer; }
        .task-content { flex: 1; }
        .task-id { color: #666; font-size: 12px; }
        .task-title { font-size: 16px; font-weight: bold; color: #333; }
        .task-desc { color: #666; margin: 5px 0; font-size: 14px; }
        .task-tags { margin-top: 5px; }
        .tag { display: inline-block; background: #e0e0e0; padding: 2px 8px; border-radius: 12px; font-size: 11px; margin-right: 4px; color: #666; }
        .task-meta { display: flex; gap: 15px; font-size: 12px; color: #999; margin-top: 8px; }
        .task-actions { position: absolute; right: 15px; top: 50%; transform: translateY(-50%); display: none; gap: 5px; }
        .task:hover .task-actions { display: flex; }
        .status-new { border-left: 4px solid #2196F3; }
        .status-in_progress { border-left: 4px solid #ff9800; }
        .status-done { border-left: 4px solid #4CAF50; background: #fafafa; }
        .status-cancelled { border-left: 4px solid #9e9e9e; background: #fafafa; }
        .empty { text-align: center; color: #999; padding: 40px; }
        .bulk-actions { display: none; background: #333; padding: 10px; border-radius: 4px; margin-bottom: 10px; }
        .bulk-actions.visible { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
        .bulk-actions span { color: white; margin-right: 10px; }
        .tag-filter { position: relative; }
        .tag-filter input { padding: 8px 12px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px; width: 200px; }
        .tag-filter input:focus { outline: none; border-color: #4CAF50; }
        .tag-suggestions { display: none; position: absolute; top: 100%; left: 0; right: 0; background: white; border: 1px solid #ddd; border-radius: 4px; margin-top: 4px; max-height: 200px; overflow-y: auto; z-index: 100; box-shadow: 0 4px 8px rgba(0,0,0,0.1); }
        .tag-suggestions.active { display: block; }
        .tag-suggestion { padding: 8px 12px; cursor: pointer; }
        .tag-suggestion:hover { background: #f0f0f0; }
        .tag-selected { display: inline-block; background: #4CAF50; color: white; padding: 4px 8px; border-radius: 12px; font-size: 12px; margin-right: 4px; margin-bottom: 4px; cursor: pointer; }
        .tag-selected:hover { background: #d32f2f; }
        .toolbar { margin-bottom: 15px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
                <h1><a href="/">📋 Task Tracker</a></h1>
                <a class="tab-btn" style="padding:8px 10px;display:inline-flex;align-items:center" href="/logout" title="Выйти" aria-label="Выйти">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true" focusable="false" xmlns="http://www.w3.org/2000/svg">
                        <path d="M15 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h9" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
                        <path d="M10 12h11" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
                        <path d="M18 9l3 3-3 3" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                    </svg>
                </a>
            </div>
            <div class="tabs">
                <a class="tab-btn {{ 'active' if tab == 'active' else '' }}" href="/?tab=active">
                    📋 Активные<span class="tab-count" id="activeCount">0</span>
                </a>
                <a class="tab-btn {{ 'active' if tab == 'done' else '' }}" href="/?tab=done">
                    ✅ Завершенные<span class="tab-count" id="doneCount">0</span>
                </a>
            </div>
        </div>

        <div class="bulk-actions" id="bulkActions">
            <span>Выбрано: <strong id="selectedCount">0</strong></span>
            <button class="btn" style="background:#4CAF50;color:white" onclick="selectAll()">Выделить все</button>
            <button class="btn btn-start" onclick="bulkAction('in_progress')">▶️ Начать</button>
            <button class="btn btn-progress" onclick="bulkAction('new')">⏸️ Отложить</button>
            <button class="btn btn-done" onclick="bulkAction('done')">✅ Завершить</button>
            <button class="btn btn-cancel" onclick="bulkAction('cancelled')">❌ Отменить</button>
            <button class="btn btn-delete" onclick="bulkDelete()">🗑️ Удалить</button>
        </div>

        <div class="toolbar">
            <div class="tag-filter" id="tagFilterContainer">
                <input type="text" id="tagInput" placeholder="Фильтр по тегам..." onfocus="showTagSuggestions()" oninput="filterTags()">
                <div class="tag-suggestions" id="tagSuggestions"></div>
            </div>
            <div id="selectedTags"></div>
        </div>

        <div id="tasks"></div>
    </div>

    <script>
        let currentTab = '{{ tab }}';
        let selectedTasks = new Set();
        let allTags = [];
        let lastUsedTags = [];
        let currentFilterTags = [];
        let displayedTaskIds = [];

        const statusIcons = {{ status_icons | tojson }};

        function loadTags() {
            fetch('/api/tags')
                .then(r => {
                    if (r.status === 401) { window.location.href = '/login'; return null; }
                    return r.json();
                })
                .then(data => {
                    if (!data) return;
                    allTags = data.tags;
                    lastUsedTags = data.last_used || [];
                    renderTagSuggestions('');
                });
        }

        function showTagSuggestions() {
            filterTags();
        }

        function filterTags() {
            const input = document.getElementById('tagInput');
            const value = input.value.toLowerCase();
            const suggestions = document.getElementById('tagSuggestions');

            let filtered = allTags.filter(tag => !currentFilterTags.includes(tag));

            if (value) {
                filtered = filtered.filter(tag => tag.toLowerCase().includes(value));
            } else if (lastUsedTags.length > 0) {
                filtered = lastUsedTags.filter(tag => !currentFilterTags.includes(tag));
            }

            if (filtered.length > 0) {
                suggestions.innerHTML = filtered.map(tag =>
                    `<div class="tag-suggestion" onclick="addFilterTag('${tag}')">${tag}</div>`
                ).join('');
                suggestions.classList.add('active');
            } else {
                suggestions.classList.remove('active');
            }
        }

        function addFilterTag(tag) {
            if (!currentFilterTags.includes(tag)) {
                currentFilterTags.push(tag);
                renderSelectedTags();
                loadTasks();
            }
            document.getElementById('tagInput').value = '';
            document.getElementById('tagSuggestions').classList.remove('active');
        }

        function removeFilterTag(tag) {
            currentFilterTags = currentFilterTags.filter(t => t !== tag);
            renderSelectedTags();
            loadTasks();
        }

        function renderSelectedTags() {
            const container = document.getElementById('selectedTags');
            container.innerHTML = currentFilterTags.map(tag =>
                `<span class="tag-selected" onclick="removeFilterTag('${tag}')">${tag} ×</span>`
            ).join('');
        }

        function hideSuggestions() {
            setTimeout(() => {
                document.getElementById('tagSuggestions').classList.remove('active');
            }, 200);
        }

        document.addEventListener('click', function(e) {
            if (!e.target.closest('.tag-filter')) {
                hideSuggestions();
            }
        });

        function loadTasks() {
            let apiUrl = '/api/tasks';
            if (currentTab === 'active') {
                apiUrl += '?status=new&status=in_progress';
            } else {
                apiUrl += '?status=done';
            }

            fetch(apiUrl)
                .then(r => {
                    if (r.status === 401) { window.location.href = '/login'; return null; }
                    return r.json();
                })
                .then(data => {
                    if (!data) return;
                    let tasks = data.tasks;

                    if (currentFilterTags.length > 0) {
                        tasks = tasks.filter(task =>
                            currentFilterTags.every(filterTag =>
                                task.tags && task.tags.includes(filterTag)
                            )
                        );
                    }

                    const activeCount = data.tasks.filter(t => t.status === 'new' || t.status === 'in_progress').length;
                    const doneCount = data.tasks.filter(t => t.status === 'done').length;

                    document.getElementById('activeCount').textContent = activeCount;
                    document.getElementById('doneCount').textContent = doneCount;

                    displayedTaskIds = tasks.map(t => t.id);
                    renderTasks(tasks);
                });
        }

        function renderTasks(tasks) {
            const container = document.getElementById('tasks');
            if (tasks.length === 0) {
                container.innerHTML = '<div class="empty">Нет задач</div>';
                return;
            }

            container.innerHTML = tasks.map(task => `
                <div class="task status-${task.status} ${selectedTasks.has(task.id) ? 'selected' : ''}" id="task-${task.id}">
                    <div class="task-row" onclick="window.location.href='/task/${task.id}'">
                        <input type="checkbox" class="task-checkbox"
                               ${task.status === 'done' ? 'disabled' : ''}
                               ${selectedTasks.has(task.id) ? 'checked' : ''}
                               onclick="event.stopPropagation(); toggleSelect(${task.id})">
                        <div class="task-content">
                            <span class="task-id">${statusIcons[task.status]} #${task.id}</span>
                            <div class="task-title">${task.title}</div>
                            ${task.description ? `<div class="task-desc">${task.description.substring(0, 100)}${task.description.length > 100 ? '...' : ''}</div>` : ''}
                            ${task.tags && task.tags.length > 0 ? `
                                <div class="task-tags">
                                    ${task.tags.map(tag => `<span class="tag">${tag}</span>`).join('')}
                                </div>
                            ` : ''}
                            <div class="task-meta">
                                <span>📅 ${task.created_at}</span>
                            </div>
                        </div>
                    </div>
                    <div class="task-actions">
                        ${getActionButtons(task)}
                    </div>
                </div>
            `).join('');
        }

        function getActionButtons(task) {
            let buttons = '';
            if (task.status === 'new') {
                buttons += `<a class="btn btn-start" href="/task/${task.id}/action?cmd=start">▶️</a>`;
                buttons += `<a class="btn btn-cancel" href="/task/${task.id}/action?cmd=cancel">❌</a>`;
            } else if (task.status === 'in_progress') {
                buttons += `<a class="btn btn-progress" href="/task/${task.id}/action?cmd=pause">⏸️</a>`;
                buttons += `<a class="btn btn-done" href="/task/${task.id}/action?cmd=done">✅</a>`;
            } else if (task.status === 'done') {
                buttons += `<a class="btn btn-reopen" href="/task/${task.id}/action?cmd=reopen">🔄</a>`;
            }
            buttons += `<a class="btn btn-delete" href="/task/${task.id}/action?cmd=delete">🗑️</a>`;
            return buttons;
        }

        function toggleSelect(taskId) {
            if (selectedTasks.has(taskId)) {
                selectedTasks.delete(taskId);
            } else {
                selectedTasks.add(taskId);
            }
            updateBulkUI();
            document.getElementById('task-' + taskId).classList.toggle('selected');
        }

        function selectAll() {
            displayedTaskIds.forEach(taskId => {
                if (!selectedTasks.has(taskId)) {
                    selectedTasks.add(taskId);
                    document.getElementById('task-' + taskId).classList.add('selected');
                    const checkbox = document.querySelector(`#task-${taskId} .task-checkbox`);
                    if (checkbox) checkbox.checked = true;
                }
            });
            updateBulkUI();
        }

        function updateBulkUI() {
            const count = selectedTasks.size;
            document.getElementById('selectedCount').textContent = count;
            document.getElementById('bulkActions').classList.toggle('visible', count > 0);
        }

        function bulkAction(status) {
            const ids = Array.from(selectedTasks);
            if (ids.length === 0) return;
            fetch('/api/tasks/bulk', {
                method: 'PATCH',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ids: ids, status: status})
            }).then(r => {
                if (r.status === 401) { window.location.href = '/login'; return; }
                selectedTasks.clear();
                updateBulkUI();
                loadTasks();
            });
        }

        function bulkDelete() {
            if (!confirm('Удалить выбранные задачи?')) return;
            const ids = Array.from(selectedTasks);
            if (ids.length === 0) return;
            fetch('/api/tasks/bulk', {
                method: 'DELETE',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ids: ids})
            }).then(r => {
                if (r.status === 401) { window.location.href = '/login'; return; }
                selectedTasks.clear();
                updateBulkUI();
                loadTags();
                loadTasks();
            });
        }

        loadTags();
        loadTasks();
    </script>
</body>
</html>
"""


TASK_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="icon" type="image/x-icon" href="{{ url_for('static', filename='favicon.ico') }}">
    <link rel="icon" type="image/png" href="{{ url_for('static', filename='favicon.png') }}">
    <title>#{{ task.id }} {{ task.title }}</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f5f5; padding: 20px; }
        .container { max-width: 800px; margin: 0 auto; }
        .breadcrumb { margin-bottom: 20px; }
        .breadcrumb a { color: #4CAF50; text-decoration: none; }
        .task { background: white; border-radius: 8px; padding: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .task-header { margin-bottom: 15px; }
        .task-id { color: #666; font-size: 14px; }
        .task-title { font-size: 24px; font-weight: bold; color: #333; margin-top: 5px; }
        .task-desc { color: #666; margin: 15px 0; font-size: 16px; line-height: 1.6; }
        .task-tags { margin: 15px 0; }
        .tag { display: inline-block; background: #e0e0e0; padding: 4px 12px; border-radius: 16px; font-size: 13px; margin-right: 6px; color: #333; cursor: pointer; }
        .tag:hover { background: #d0d0d0; }
        .tag-add { background: transparent; border: 1px dashed #999; color: #999; }
        .tag-add:hover { border-color: #4CAF50; color: #4CAF50; }
        .task-meta { display: flex; gap: 20px; font-size: 14px; color: #999; margin-top: 20px; padding-top: 20px; border-top: 1px solid #eee; }
        .status-new { border-left: 4px solid #2196F3; }
        .status-in_progress { border-left: 4px solid #ff9800; }
        .status-done { border-left: 4px solid #4CAF50; }
        .status-cancelled { border-left: 4px solid #9e9e9e; }
        .actions { display: flex; gap: 10px; margin-top: 20px; flex-wrap: wrap; }
        .btn { padding: 10px 20px; border: none; border-radius: 4px; cursor: pointer; font-size: 14px; text-decoration: none; display: inline-block; }
        .btn-start { background: #2196F3; color: white; }
        .btn-progress { background: #ff9800; color: white; }
        .btn-done { background: #4CAF50; color: white; }
        .btn-cancel { background: #9e9e9e; color: white; }
        .btn-reopen { background: #9c27b0; color: white; }
        .btn-delete { background: #f44336; color: white; }
        .btn-edit { background: #607d8b; color: white; }
        .modal { display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.5); z-index: 100; }
        .modal.active { display: flex; align-items: center; justify-content: center; }
        .modal-content { background: white; padding: 20px; border-radius: 8px; width: 300px; }
        .modal-content h3 { margin-bottom: 15px; }
        .modal-content input { width: 100%; padding: 8px; margin-bottom: 10px; border: 1px solid #ddd; border-radius: 4px; }
        .modal-actions { display: flex; gap: 10px; justify-content: flex-end; }
        .btn-small { padding: 5px 10px; font-size: 12px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="breadcrumb">
            <a href="{{ back_url }}">← Список задач</a>
        </div>

        <div class="task status-{{ task.status.value }}">
            <div class="task-header">
                <span class="task-id">{{ status_icon }} #{{ task.id }} • {{ task.status.value }}</span>
                <div class="task-title">{{ task.title }}</div>
            </div>
            {% if task.description %}
            <div class="task-desc">{{ task.description }}</div>
            {% endif %}

            <div class="task-tags" id="tagsContainer">
                {% if task.tags %}
                    {% for tag in task.tags %}
                        <span class="tag" onclick="removeTag('{{ tag }}')">{{ tag }} ×</span>
                    {% endfor %}
                {% endif %}
                <span class="tag tag-add" onclick="showAddTag()">+ Тег</span>
            </div>

            <div class="task-meta">
                <span>📅 Создано: {{ task.created_at.strftime('%d.%m.%Y %H:%M') }}</span>
            </div>
            <div class="actions">
                {{ action_buttons|safe }}
                <a class="btn btn-edit" href="/task/{{ task.id }}/edit">✏️ Редактировать</a>
            </div>
        </div>
    </div>

    <div class="modal" id="tagModal">
        <div class="modal-content">
            <h3>Добавить тег</h3>
            <input type="text" id="newTagInput" placeholder="Название тега" onkeypress="if(event.key==='Enter')addTag()">
            <div class="modal-actions">
                <button class="btn btn-small" style="background:#e0e0e0;color:#333" onclick="closeModal()">Отмена</button>
                <button class="btn btn-small btn-start" onclick="addTag()">Добавить</button>
            </div>
        </div>
    </div>

    <script>
        function showAddTag() {
            document.getElementById('tagModal').classList.add('active');
            document.getElementById('newTagInput').focus();
        }

        function closeModal() {
            document.getElementById('tagModal').classList.remove('active');
            document.getElementById('newTagInput').value = '';
        }

        function addTag() {
            const tag = document.getElementById('newTagInput').value.trim();
            if (!tag) return;

            fetch('/api/tasks/{{ task.id }}/tags', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({tag: tag})
            }).then(() => location.reload());
        }

        function removeTag(tag) {
            if (!confirm('Удалить тег "' + tag + '"?')) return;

            fetch('/api/tasks/{{ task.id }}/tags', {
                method: 'DELETE',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({tag: tag})
            }).then(() => location.reload());
        }
    </script>
</body>
</html>
"""


EDIT_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="icon" type="image/x-icon" href="{{ url_for('static', filename='favicon.ico') }}">
    <link rel="icon" type="image/png" href="{{ url_for('static', filename='favicon.png') }}">
    <title>Редактирование #{{ task.id }}</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f5f5; padding: 20px; }
        .container { max-width: 800px; margin: 0 auto; }
        .breadcrumb { margin-bottom: 20px; }
        .breadcrumb a { color: #4CAF50; text-decoration: none; }
        .form { background: white; border-radius: 8px; padding: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .form-group { margin-bottom: 15px; }
        .form-group label { display: block; margin-bottom: 5px; color: #666; font-size: 14px; font-weight: bold; }
        .form-group input, .form-group textarea { width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 4px; font-size: 16px; font-family: inherit; }
        .form-group textarea { min-height: 200px; }
        .actions { display: flex; gap: 10px; margin-top: 20px; }
        .btn { padding: 10px 20px; border: none; border-radius: 4px; cursor: pointer; font-size: 14px; text-decoration: none; display: inline-block; }
        .btn-save { background: #4CAF50; color: white; }
        .btn-cancel { background: #e0e0e0; color: #333; }
    </style>
</head>
<body>
    <div class="container">
        <div class="breadcrumb">
            <a href="/task/{{ task.id }}">← Назад к задаче</a>
        </div>

        <form class="form" method="POST" action="/task/{{ task.id }}/edit">
            <h2 style="margin-bottom: 20px;">Редактирование задачи #{{ task.id }}</h2>

            <div class="form-group">
                <label>Название:</label>
                <input type="text" name="title" value="{{ task.title }}" required>
            </div>

            <div class="form-group">
                <label>Описание:</label>
                <textarea name="description">{{ task.description or '' }}</textarea>
            </div>

            <div class="actions">
                <button type="submit" class="btn btn-save">💾 Сохранить</button>
                <a class="btn btn-cancel" href="/task/{{ task.id }}">Отмена</a>
            </div>
        </form>
    </div>
</body>
</html>
"""


def get_action_buttons(task):
    buttons = []
    if task.status == TaskStatus.NEW:
        buttons.append(f'<a class="btn btn-start" href="/task/{task.id}/action?cmd=start">▶️ Начать</a>')
        buttons.append(f'<a class="btn btn-cancel" href="/task/{task.id}/action?cmd=cancel">❌ Отменить</a>')
    elif task.status == TaskStatus.IN_PROGRESS:
        buttons.append(f'<a class="btn btn-progress" href="/task/{task.id}/action?cmd=pause">⏸️ Отложить</a>')
        buttons.append(f'<a class="btn btn-done" href="/task/{task.id}/action?cmd=done">✅ Завершить</a>')
    elif task.status == TaskStatus.DONE:
        buttons.append(f'<a class="btn btn-reopen" href="/task/{task.id}/action?cmd=reopen">🔄 Переоткрыть</a>')
    return '\n'.join(buttons)

def _oauth_configured() -> bool:
    return bool(
        TASKTRACKER_GOOGLE_CLIENT_ID
        and TASKTRACKER_GOOGLE_CLIENT_SECRET
        and TASKTRACKER_GOOGLE_REDIRECT_URI
        and TASKTRACKER_ALLOWED_EMAILS
    )


def _json_unauthorized():
    return jsonify({"error": "unauthorized"}), 401


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if session.get("user"):
            return fn(*args, **kwargs)
        if request.path.startswith("/api/"):
            return _json_unauthorized()
        return redirect(url_for("login", next=request.full_path))

    return wrapper


@app.route("/login")
def login():
    error = request.args.get("error")
    next_url = request.args.get("next") or "/"
    if not _oauth_configured() and not error:
        error = "OAuth не настроен: задайте TASKTRACKER_ALLOWED_EMAILS, TASKTRACKER_GOOGLE_CLIENT_ID, TASKTRACKER_GOOGLE_CLIENT_SECRET, TASKTRACKER_SECRET_KEY"
    return render_template_string(LOGIN_TEMPLATE, error=error, next_url=next_url)


@app.route("/auth/google")
def auth_google():
    if not _oauth_configured():
        return redirect(url_for("login", error="OAuth не настроен"))

    state = secrets.token_urlsafe(32)
    session["google_oauth_state"] = state
    session["post_login_next"] = request.args.get("next") or "/"

    params = {
        "client_id": TASKTRACKER_GOOGLE_CLIENT_ID,
        "redirect_uri": TASKTRACKER_GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "online",
        "prompt": "select_account",
        "state": state,
    }
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)
    return redirect(url)


@app.route("/auth/google/callback")
def auth_google_callback():
    if not _oauth_configured():
        return redirect(url_for("login", error="OAuth не настроен"))

    state = request.args.get("state", "")
    expected_state = session.get("google_oauth_state", "")
    if not state or not expected_state or state != expected_state:
        session.clear()
        return redirect(url_for("login", error="Некорректный state"))

    code = request.args.get("code")
    if not code:
        session.clear()
        return redirect(url_for("login", error="Нет кода авторизации"))

    token_resp = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": TASKTRACKER_GOOGLE_CLIENT_ID,
            "client_secret": TASKTRACKER_GOOGLE_CLIENT_SECRET,
            "redirect_uri": TASKTRACKER_GOOGLE_REDIRECT_URI,
            "grant_type": "authorization_code",
        },
        timeout=10.0,
    )
    if token_resp.status_code != 200:
        session.clear()
        return redirect(url_for("login", error="Не удалось получить токен"))

    token = token_resp.json()
    access_token = token.get("access_token")
    if not access_token:
        session.clear()
        return redirect(url_for("login", error="Нет access_token"))

    userinfo_resp = httpx.get(
        "https://openidconnect.googleapis.com/v1/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10.0,
    )
    if userinfo_resp.status_code != 200:
        session.clear()
        return redirect(url_for("login", error="Не удалось получить профиль"))

    userinfo = userinfo_resp.json()
    email = (userinfo.get("email") or "").strip().lower()
    email_verified = bool(userinfo.get("email_verified"))
    if not email or not email_verified:
        session.clear()
        return redirect(url_for("login", error="Email не подтверждён"))

    if email not in TASKTRACKER_ALLOWED_EMAILS:
        session.clear()
        return redirect(url_for("login", error="Доступ запрещён"))

    session["user"] = {
        "email": email,
        "name": userinfo.get("name") or "",
        "picture": userinfo.get("picture") or "",
    }
    next_url = session.pop("post_login_next", "/") or "/"
    session.pop("google_oauth_state", None)
    return redirect(next_url)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    tab = request.args.get('tab', 'active')
    return render_template_string(
        LIST_TEMPLATE,
        tab=tab,
        status_icons=STATUS_ICONS,
    )


@app.route("/task/<int:task_id>")
@login_required
def task_view(task_id):
    task = get_task_by_id(task_id)
    if not task:
        return "Задача не найдена", 404

    back_url = "/?tab=active" if task.status in (TaskStatus.NEW, TaskStatus.IN_PROGRESS) else "/?tab=done"

    return render_template_string(
        TASK_TEMPLATE,
        task=task,
        status_icon=STATUS_ICONS.get(task.status.value, '📋'),
        action_buttons=get_action_buttons(task),
        back_url=back_url
    )


@app.route("/task/<int:task_id>/edit", methods=["GET"])
@login_required
def task_edit(task_id):
    task = get_task_by_id(task_id)
    if not task:
        return "Задача не найдена", 404

    return render_template_string(
        EDIT_TEMPLATE,
        task=task
    )


@app.route("/task/<int:task_id>/edit", methods=["POST"])
@login_required
def task_update(task_id):
    task = get_task_by_id(task_id)
    if not task:
        return "Задача не найдена", 404

    title = request.form.get('title', '').strip()
    description = request.form.get('description', '').strip()

    if title:
        update_task(task_id, title, description)

    return redirect(f'/task/{task_id}')


@app.route("/task/<int:task_id>/action")
@login_required
def task_action(task_id):
    cmd = request.args.get('cmd')

    if cmd == 'start':
        update_task_status(task_id, 'in_progress')
    elif cmd == 'pause':
        update_task_status(task_id, 'new')
    elif cmd == 'done':
        update_task_status(task_id, 'done')
    elif cmd == 'cancel':
        update_task_status(task_id, 'cancelled')
    elif cmd == 'reopen':
        update_task_status(task_id, 'new')
    elif cmd == 'delete':
        delete_task(task_id)
        cleanup_unused_tags()
        return redirect('/')

    return redirect(f'/task/{task_id}')


@app.route("/api/tasks")
@login_required
def api_tasks():
    status_filter = request.args.getlist('status')

    if status_filter:
        if 'new' in status_filter and 'in_progress' in status_filter:
            tasks = get_all_tasks()
            tasks = [t for t in tasks if t.status in (TaskStatus.NEW, TaskStatus.IN_PROGRESS)]
        elif status_filter == ['done']:
            tasks = get_all_tasks(status='done')
        else:
            tasks = get_all_tasks()
    else:
        tasks = get_all_tasks()

    return jsonify({
        "tasks": [
            {
                "id": t.id,
                "title": t.title,
                "description": t.description,
                "status": t.status.value,
                "priority": t.priority.value,
                "created_by": t.created_by,
                "created_at": t.created_at.strftime("%d.%m.%Y %H:%M"),
                "tags": t.tags or []
            }
            for t in tasks
        ]
    })


@app.route("/api/tags")
@login_required
def api_tags():
    tags = get_all_tags()
    last_used = tags[-5:] if len(tags) > 5 else tags
    return jsonify({"tags": tags, "last_used": last_used})


@app.route("/api/tasks/<int:task_id>", methods=["PATCH"])
@login_required
def api_update_task(task_id):
    data = request.json
    if "status" in data:
        update_task_status(task_id, data["status"])
    return jsonify({"success": True})


@app.route("/api/tasks/<int:task_id>", methods=["DELETE"])
@login_required
def api_delete_task(task_id):
    delete_task(task_id)
    cleanup_unused_tags()
    return jsonify({"success": True})


@app.route("/api/tasks/bulk", methods=["PATCH"])
@login_required
def api_bulk_update():
    data = request.json
    ids = data.get('ids', [])
    status = data.get('status')
    if ids and status:
        bulk_update_status(ids, status)
    return jsonify({"success": True})


@app.route("/api/tasks/bulk", methods=["DELETE"])
@login_required
def api_bulk_delete():
    data = request.json
    ids = data.get('ids', [])
    if ids:
        bulk_delete(ids)
    return jsonify({"success": True})


@app.route("/api/tasks/<int:task_id>/tags", methods=["POST"])
@login_required
def api_add_tag(task_id):
    data = request.json
    tag = data.get('tag', '').strip()
    if tag:
        current_tags = get_task_by_id(task_id).tags or []
        set_task_tags(task_id, current_tags + [tag])
    return jsonify({"success": True})


@app.route("/api/tasks/<int:task_id>/tags", methods=["DELETE"])
@login_required
def api_remove_tag(task_id):
    data = request.json
    tag = data.get('tag', '').strip()
    task = get_task_by_id(task_id)
    if tag and task and task.tags and tag in task.tags:
        new_tags = [t for t in task.tags if t != tag]
        set_task_tags(task_id, new_tags)
        cleanup_unused_tags()
    return jsonify({"success": True})


def run_web():
    app.run(host="0.0.0.0", port=WEB_PORT, debug=False)


if __name__ == "__main__":
    run_web()
