"""Browser-side application behavior."""

APP_JS = r"""
window.AICF = window.AICF || {};
window.AICF.readJsonResponse = async function(response, url) {
    var data;
    try {
        var text = await response.text();
        data = JSON.parse(text);
        if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error('invalid response');
    } catch (error) {
        var path = String(url || '').split('?')[0];
        var failure = new Error((response.status >= 500 ? '后台服务异常' : '接口未返回有效 JSON') +
            '（HTTP ' + response.status + '，' + path + '）。当前编辑内容已保留，请查看后台错误日志。');
        failure.status = response.status; failure.code = 'INVALID_API_RESPONSE'; failure.endpoint = path;
        throw failure;
    }
    return data;
};
window.AICF.bindProjectAssetCardSelection = function() {
    if (window.__AICF_PROJECT_ASSET_SELECTION_BOUND__) return;
    window.__AICF_PROJECT_ASSET_SELECTION_BOUND__ = true;
    var previewDialog = document.createElement('dialog');
    previewDialog.className = 'aicf-asset-image-preview';
    previewDialog.setAttribute('aria-label', '素材图片放大预览');
    previewDialog.innerHTML = '<button type="button" class="aicf-asset-preview-close" aria-label="关闭图片预览">×</button><img alt="素材图片"><p></p>';
    document.body.appendChild(previewDialog);
    var previewTrigger = null;
    var closePreview = function() { previewDialog.close(); };
    previewDialog.querySelector('button').addEventListener('click', closePreview);
    previewDialog.addEventListener('click', function(event) { if(event.target === previewDialog) closePreview(); });
    previewDialog.addEventListener('close', function() {
        previewDialog.querySelector('img').removeAttribute('src');
        if(previewTrigger && previewTrigger.isConnected) previewTrigger.focus();
    });
    var openPreview = function(control) {
        var card = control.closest('.thumbnail-item');
        var image = card && card.querySelector('img');
        if(!image) return;
        previewTrigger = control;
        var caption = card.querySelector('.caption, figcaption');
        previewDialog.querySelector('img').src = image.currentSrc || image.src;
        previewDialog.querySelector('img').alt = image.alt || '素材图片';
        previewDialog.querySelector('p').textContent = caption ? caption.textContent : image.alt || '';
        previewDialog.showModal();
    };
    var decoratePreviews = function() {
        document.querySelectorAll('#project_assets_page .project-assets-gallery .thumbnail-item').forEach(function(card) {
            if(card.querySelector('.aicf-asset-preview-trigger')) return;
            // Gallery cards are buttons; use an accessible span to avoid nested buttons.
            var control = document.createElement('span');
            control.className = 'aicf-asset-preview-trigger';
            control.setAttribute('role', 'button'); control.tabIndex = 0;
            control.setAttribute('aria-label', '放大预览图片'); control.title = '放大预览';
            control.innerHTML = '<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path d="M14 4h6v6M20 4l-7 7M10 20H4v-6M4 20l7-7" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
            card.appendChild(control);
        });
    };
    new MutationObserver(decoratePreviews).observe(document.body, {childList:true, subtree:true});
    decoratePreviews();
    document.addEventListener('keydown', function(event) {
        var control = event.target.closest && event.target.closest('.aicf-asset-preview-trigger');
        if(control && (event.key === 'Enter' || event.key === ' ')) {
            event.preventDefault(); event.stopImmediatePropagation(); openPreview(control);
        }
    }, true);
    var clearSelection = function() {
        document.querySelectorAll('#project_assets_page .project-assets-gallery .thumbnail-item.aicf-project-asset-selected').forEach(function(card) {
            card.classList.remove('aicf-project-asset-selected');
            card.style.removeProperty('outline');
            card.style.removeProperty('outline-offset');
        });
    };
    document.addEventListener('click', function(event) {
        var target = event.target;
        if (!target || !target.closest) return;
        var previewControl = target.closest('.aicf-asset-preview-trigger');
        if(previewControl) {
            event.preventDefault(); event.stopImmediatePropagation(); openPreview(previewControl); return;
        }
        if (target.closest('#project_assets_project, #project_assets_open_btn, #close_project_assets_btn, #project_assets_save_owners_btn, #project_assets_unbind_btn')) {
            clearSelection();
            return;
        }
        var card = target.closest('#project_assets_page .project-assets-gallery .thumbnail-item');
        if (!card) return;
        var selected = card.classList.toggle('aicf-project-asset-selected');
        if (selected) {
            card.style.setProperty('outline', '3px solid var(--success)', 'important');
            card.style.setProperty('outline-offset', '-3px', 'important');
        } else {
            card.style.removeProperty('outline');
            card.style.removeProperty('outline-offset');
        }
    }, true);
    document.addEventListener('change', function(event) {
        var target = event.target;
        if (target && target.closest && target.closest('#project_assets_project')) clearSelection();
    }, true);
};
window.toggleDevDrawer = function() {
    var overlay = document.getElementById('dev-overlay');
    var drawer = document.getElementById('settings-panel') || document.getElementById('dev-drawer');
    if (overlay) overlay.classList.toggle('open');
    if (drawer) drawer.classList.toggle('open');
};
window.switchDevTab = function(evt, tabId) {
    var panel = document.getElementById('settings-panel') || document.getElementById('settings_page') || document;
    var targetId = String(tabId || 'dev-tab1');
    panel.querySelectorAll('.dev-tab-btn').forEach(function(button) {
        var active = button.dataset && button.dataset.devTab === targetId;
        button.classList.toggle('active', active);
        button.setAttribute('aria-selected', active ? 'true' : 'false');
    });
    if (evt && evt.currentTarget) {
        evt.currentTarget.classList.add('active');
    }
};
function initAICF() {
window.AICF.bindProjectAssetCardSelection();
window.AICF.bindTargetBeatCountInput = function() {
    var proxy = document.getElementById('target_beat_count_proxy');
    var source = document.querySelector('#target_beat_count_box textarea, #target_beat_count_box input');
    if (!proxy || !source) return;
    if (!proxy.value && source.value) proxy.value = source.value;
    if (proxy._targetBeatCountBound) return;
    proxy._targetBeatCountBound = true;
    proxy.addEventListener('input', function() {
        var value = proxy.value;
        var setter = Object.getOwnPropertyDescriptor(
            source.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype,
            'value',
        );
        if (setter && setter.set) setter.set.call(source, value);
        else source.value = value;
        source.dispatchEvent(new Event('input', { bubbles: true }));
        source.dispatchEvent(new Event('change', { bubbles: true }));
    });
};
window.AICFRunTimer = window.AICFRunTimer || {
    format: function(seconds) {
        seconds = Math.max(0, Math.floor(Number(seconds) || 0));
        var h = String(Math.floor(seconds / 3600)).padStart(2, '0');
        var m = String(Math.floor((seconds % 3600) / 60)).padStart(2, '0');
        var s = String(seconds % 60).padStart(2, '0');
        return h + ':' + m + ':' + s;
    },
    update: function() {
        var el = document.querySelector('.pipeline-elapsed');
        if (!el) return;
        var running = el.dataset.running === 'true';
        var startTime = Number(el.dataset.startTime || 0);
        var elapsed = Number(el.dataset.elapsed || 0);
        if (running && startTime > 0) {
            elapsed = Math.max(elapsed, Math.floor(Date.now() / 1000 - startTime));
        }
        var value = el.querySelector('strong');
        if (value) value.textContent = this.format(elapsed);
    }
};
if (!window.__AICF_RUN_TIMER_INTERVAL__) {
    window.__AICF_RUN_TIMER_INTERVAL__ = setInterval(function() {
        window.AICFRunTimer.update();
    }, 1000);
}
window.AICFRunTimer.update();
window.AICFPreview = window.AICFPreview || {
    _timer: null,
    _overlay: null,
    _ensureOverlay: function() {
        if (this._overlay) return this._overlay;
        var ov = document.createElement('div');
        ov.id = 'key-img-preview-overlay';
        ov.className = 'key-img-preview-overlay';
        ov.innerHTML =
            '<div class=\"key-img-preview-container\">' +
            '<button class=\"key-img-preview-close\" onclick=\"window.AICFPreview.close()\">&#10005;</button>' +
            '<img class=\"key-img-preview-img\" src=\"\" alt=\"preview\">' +
            '<video class=\"key-img-preview-img\" controls style=\"display:none\"></video>' +
            '<div class=\"key-img-preview-info\"></div>' +
            '</div>';
        ov.addEventListener('click', function(ev) { if (ev.target === ov) window.AICFPreview.close(); });
        document.body.appendChild(ov);
        this._overlay = ov;
        return ov;
    },
    image: function(imgEl) {
        // Single-click (250ms delay): preview large image
        var self = this;
        if (self._timer) { clearTimeout(self._timer); self._timer = null; self._dblClick(imgEl); return; }
        self._timer = setTimeout(function() {
            self._timer = null;
            var ov = self._ensureOverlay();
            var img = ov.querySelector('img.key-img-preview-img');
            var vid = ov.querySelector('video');
            var info = ov.querySelector('.key-img-preview-info');
            if (vid) { vid.style.display = 'none'; vid.pause(); vid.removeAttribute('src'); vid.load(); }
            if (img) { img.style.display = ''; img.src = imgEl.src; }
            if (info) {
                info.style.display = '';
                info.innerHTML = 'Part: ' + (imgEl.dataset.partId || '') +
                    ' | Shot: ' + (imgEl.dataset.shot || '') +
                    ' | Guide: ' + (imgEl.dataset.guideIndex || '') +
                    (imgEl.dataset.forceNewGuide === 'true' ? ' | force_new_guide' : '');
            }
            ov.classList.add('open');
        }, 250);
    },
    _dblClick: function(imgEl) {
        // Double-click: regenerate single key image
        var payload = imgEl.dataset.partId + '|' + imgEl.dataset.guideIndex;
        console.log('[key-img] dblclick regen:', payload);
        if (typeof window.triggerKeyImgRegen === 'function') window.triggerKeyImgRegen(payload);
    },
    video: function(url) {
        var ov = this._ensureOverlay();
        var img = ov.querySelector('img.key-img-preview-img');
        var vid = ov.querySelector('video');
        var info = ov.querySelector('.key-img-preview-info');
        if (img) img.style.display = 'none';
        if (info) info.style.display = 'none';
        if (vid) { vid.style.display = ''; vid.src = url; vid.load(); }
        ov.classList.add('open');
    },
    close: function() {
        var ov = this._overlay;
        if (!ov) return;
        ov.classList.remove('open');
        var vid = ov.querySelector('video');
        if (vid) { vid.pause(); vid.removeAttribute('src'); vid.load(); }
        var img = ov.querySelector('img.key-img-preview-img');
        if (img) img.style.display = '';
        var info = ov.querySelector('.key-img-preview-info');
        if (info) info.style.display = '';
    }
};
// Legacy aliases for existing onclick handlers
window._handleKeyImgClick = function(e) { window.AICFPreview.image(e.currentTarget); };
window._previewVideo = function(url) { window.AICFPreview.video(url); };
window._closePreview = function() { window.AICFPreview.close(); };
window._previewKeyImg = function(img) { window.AICFPreview.image(img); };
window.previewLatestFinalVideo = async function() {
    var activeEpisode = document.querySelector('.episode-cell.active[data-video-url]');
    if (activeEpisode && activeEpisode.dataset.videoUrl) {
        window._previewVideo(activeEpisode.dataset.videoUrl);
        return;
    }
    var firstEpisodeVideo = document.querySelector('.episode-cell[data-video-url]:not([data-video-url=""])');
    if (firstEpisodeVideo && firstEpisodeVideo.dataset.videoUrl) {
        window._previewVideo(firstEpisodeVideo.dataset.videoUrl);
        return;
    }
    try {
        if (window.AICF && window.AICF.refreshEpisodePicker) window.AICF.refreshEpisodePicker();
        var response = await fetch('/api/final_video/latest', {cache: 'no-store'});
        var data = await response.json();
        if (data && data.ok && data.url) {
            window._previewVideo(data.url);
            return;
        }
    } catch (error) {
        console.warn('final video latest api failed', error);
    }
    alert('No final video available for preview yet.');
};
window.openCurrentVideosDir = async function() {
    var btn = window.AICF && window.AICF.findGradioBtn ? window.AICF.findGradioBtn('open_videos_dir_btn') : null;
    if (btn) {
        btn.click();
        return;
    }
    try {
        var response = await fetch('/api/project/open_videos_dir', {method: 'POST', cache: 'no-store'});
        var data = await response.json();
        if (!response.ok || !data || !data.ok) {
            throw new Error((data && data.message) || 'Open video directory failed');
        }
    } catch (error) {
        console.warn('open videos dir failed', error);
        alert(error && error.message ? error.message : 'Open video directory failed');
    }
};

// ---- AICF UI namespace (dev drawer, tabs) ----
window.AICF = window.AICF || {};
window.AICF.archiveReadOnly = false;
window.AICF.setArchiveReadOnly = function(locked, episodeName) {
    locked = !!locked;
    window.AICF.archiveReadOnly = locked;
    document.body.classList.toggle('archive-readonly', locked);
    document.body.dataset.currentEpisode = episodeName || '';

    document.querySelectorAll('#story_editor textarea, #beats_editor textarea, .segment-director-editor').forEach(function(el) {
        el.readOnly = locked;
        el.setAttribute('aria-readonly', locked ? 'true' : 'false');
        if (el._aicfCmState && el._aicfCmState.cm) {
            el._aicfCmState.cm.setOption('readOnly', locked ? 'nocursor' : false);
        }
    });

    var blockedLabels = [
        'Generate story',
        'Split beats',
        'Generate segment prompts',
        'Generate video',
        'Merge final video',
        'One click',
        'Save story',
        'Detect risk',
        'Split complex beat',
        'Save assets',
        'Save prompt',
        'Regenerate prompt',
        'Regenerate video',
        'Fullscreen edit'
    ];
    document.querySelectorAll('button').forEach(function(btn) {
        if (btn.classList && btn.classList.contains('episode-cell')) return;
        var label = (btn.textContent || '').replace(/\s+/g, ' ').trim();
        var shouldBlock = blockedLabels.some(function(text) { return label.indexOf(text) >= 0; });
        if (!shouldBlock) return;
        btn.classList.toggle('archive-disabled', locked);
        if (locked) {
            btn.dataset.archivePreviousDisabled = btn.disabled ? '1' : '0';
            btn.disabled = true;
            btn.dataset.archiveReadonlyTitle = btn.getAttribute('title') || '';
            btn.setAttribute('title', '宸插綊妗ｇ殑闆嗘暟鍙兘鏌ョ湅');
        } else if (Object.prototype.hasOwnProperty.call(btn.dataset, 'archiveReadonlyTitle')) {
            btn.disabled = btn.dataset.archivePreviousDisabled === '1';
            var oldTitle = btn.dataset.archiveReadonlyTitle || '';
            if (oldTitle) btn.setAttribute('title', oldTitle);
            else btn.removeAttribute('title');
            delete btn.dataset.archiveReadonlyTitle;
            delete btn.dataset.archivePreviousDisabled;
        }
    });
};
window.AICF.refreshEpisodePicker = async function() {
    var grid = document.getElementById('episode-grid');
    if (!grid) return;
    try {
        var response = await fetch('/api/project/current_state', {cache: 'no-store'});
        if (!response.ok) return;
        var data = await response.json();
        var current = data.current_episode || '';
        // A named episode can still be an editable draft. The server's
        // readonly flag distinguishes archived episodes from active drafts.
        window.AICF.setArchiveReadOnly(Boolean(data.readonly), current);
        var apiItems = Array.isArray(data.episode_items) ? data.episode_items : [];
        var episodes = Array.isArray(data.episodes) ? data.episodes : [];
        var currentItem = apiItems.find(function(item) {
            return !String((item && item.value) || '');
        });
        var archivedItems = apiItems.filter(function(item) {
            return String((item && item.value) || '') !== '';
        });
        var items = [{
            label: '当前生成',
            value: '',
            video_url: String((currentItem && currentItem.video_url) || '')
        }].concat(archivedItems.map(function(item, idx) {
            var value = String(item.value || '');
            var digits = value.replace(/\D/g, '');
            var label = digits ? String(parseInt(digits, 10) || idx + 1) : String(item.label || idx + 1);
            return {label: label, value: value, video_url: String(item.video_url || '')};
        }));
        if (!apiItems.length) {
            episodes.forEach(function(name, idx) {
                var n = String(idx + 1);
                var digits = String(name || '').replace(/\D/g, '');
                var label = digits ? String(parseInt(digits, 10) || n) : n;
                items.push({label: label, value: String(name || ''), video_url: ''});
            });
        }
        grid.innerHTML = items.map(function(item) {
            var active = item.value === current ? ' active' : '';
            var title = item.value ? item.value : '当前生成（未归档）';
            return '<button type="button" class="episode-cell' + active + '" data-episode="' + item.value.replace(/"/g, '&quot;') + '" data-video-url="' + String(item.video_url || '').replace(/"/g, '&quot;') + '" title="' + title.replace(/"/g, '&quot;') + '"><b>' + item.label + '</b></button>';
        }).join('');
        grid.querySelectorAll('.episode-cell').forEach(function(btn) {
            btn.addEventListener('click', function(ev) {
                ev.preventDefault();
                window.AICF.markEpisodeButtonActive(btn.dataset.episode || '');
                window.AICF.selectEpisode(btn.dataset.episode || '', btn.dataset.videoUrl || '');
            });
        });
    } catch (error) {
        console.warn('episode picker refresh failed', error);
    }
};
window.AICF.markEpisodeButtonActive = function(episode) {
    var grid = document.getElementById('episode-grid');
    if (!grid) return;
    var target = String(episode || '');
    grid.querySelectorAll('.episode-cell').forEach(function(btn) {
        var selected = String(btn.dataset.episode || '') === target;
        btn.classList.toggle('active', selected);
        btn.setAttribute('aria-pressed', selected ? 'true' : 'false');
    });
};
window.AICF.toggleEpisodePicker = function(event) {
    if (event) event.preventDefault();
    window.AICF.refreshEpisodePicker();
    return false;
};
window.AICF.selectEpisode = function(episode, previewUrl) {
    window.AICF.markEpisodeButtonActive(episode || '');
    var box = window.AICF.findGradioInput ? window.AICF.findGradioInput('episode_select_payload') : null;
    var btn = window.AICF.findGradioBtn ? window.AICF.findGradioBtn('episode_select_btn') : null;
    if (!box || !btn) { console.warn('episode select trigger not found'); return false; }
    box.value = String(episode || '');
    box.dispatchEvent(new Event('input', {bubbles: true}));
    box.dispatchEvent(new Event('change', {bubbles: true}));
    setTimeout(function() { btn.click(); }, 80);
    if (previewUrl) {
        setTimeout(function() { window._previewVideo(previewUrl); }, 180);
    }
    setTimeout(function() { window.AICF.refreshEpisodePicker(); }, 500);
    return false;
};
setTimeout(function() { window.AICF.refreshEpisodePicker(); }, 300);
window.AICF.theme = window.AICF.theme || {
    fallback: 'linear',
    active: 'linear',
    themes: [
        ['linear', 'Linear'],
        ['cursor', 'Cursor'],
        ['vercel', 'Vercel'],
        ['glass', 'Glass'],
        ['ocean', 'Ocean'],
        ['purple', 'Purple AI'],
        ['cyber', 'Cyber'],
        ['classic', 'Classic']
    ],
    tokens: {
        linear: {
            '--theme-bg': '#f7f8fa',
            '--theme-bg-subtle': '#f4f6f8',
            '--theme-surface': '#ffffff',
            '--theme-surface-subtle': '#f9fafb',
            '--theme-surface-muted': '#f1f5f9',
            '--theme-border': '#e5e7eb',
            '--theme-border-strong': '#cbd5e1',
            '--theme-text-title': '#0f172a',
            '--theme-text-card': '#1f2937',
            '--theme-text-body': '#374151',
            '--theme-text-secondary': '#6b7280',
            '--theme-text-label': '#4b5563',
            '--theme-accent': '#2563eb',
            '--theme-accent-hover': '#1d4ed8',
            '--theme-accent-soft': '#eff6ff',
            '--theme-accent-border': '#bfdbfe',
            '--theme-accent-border-strong': '#93c5fd',
            '--theme-accent-soft-hover': '#dbeafe'
        },
        cursor: {
            '--theme-bg': '#f7f7fb',
            '--theme-bg-subtle': '#f1f2f8',
            '--theme-surface': '#ffffff',
            '--theme-surface-subtle': '#f7f6ff',
            '--theme-surface-muted': '#ede9fe',
            '--theme-border': '#dedaf0',
            '--theme-border-strong': '#bcb4d8',
            '--theme-text-title': '#18181b',
            '--theme-text-card': '#27272a',
            '--theme-text-body': '#3f3f46',
            '--theme-text-secondary': '#71717a',
            '--theme-text-label': '#52525b',
            '--theme-accent': '#8b5cf6',
            '--theme-accent-hover': '#7c3aed',
            '--theme-accent-soft': '#f3e8ff',
            '--theme-accent-border': '#ddd6fe',
            '--theme-accent-border-strong': '#c4b5fd',
            '--theme-accent-soft-hover': '#ede9fe'
        },
        vercel: {
            '--theme-bg': '#ffffff',
            '--theme-bg-subtle': '#fafafa',
            '--theme-surface': '#ffffff',
            '--theme-surface-subtle': '#fafafa',
            '--theme-surface-muted': '#f4f4f5',
            '--theme-border': '#e4e4e7',
            '--theme-border-strong': '#a1a1aa',
            '--theme-text-title': '#000000',
            '--theme-text-card': '#18181b',
            '--theme-text-body': '#27272a',
            '--theme-text-secondary': '#71717a',
            '--theme-text-label': '#3f3f46',
            '--theme-accent': '#000000',
            '--theme-accent-hover': '#27272a',
            '--theme-accent-soft': '#f4f4f5',
            '--theme-accent-border': '#d4d4d8',
            '--theme-accent-border-strong': '#a1a1aa',
            '--theme-accent-soft-hover': '#e4e4e7'
        },
        glass: {
            '--theme-bg': '#eef2ff',
            '--theme-bg-subtle': '#f8fafc',
            '--theme-surface': 'rgba(255, 255, 255, 0.78)',
            '--theme-surface-subtle': 'rgba(255, 255, 255, 0.54)',
            '--theme-surface-muted': 'rgba(238, 242, 255, 0.72)',
            '--theme-border': 'rgba(148, 163, 184, 0.36)',
            '--theme-border-strong': 'rgba(99, 102, 241, 0.32)',
            '--theme-accent': '#6366f1',
            '--theme-accent-hover': '#4f46e5',
            '--theme-accent-soft': 'rgba(99, 102, 241, 0.12)',
            '--theme-accent-border': 'rgba(129, 140, 248, 0.34)',
            '--theme-accent-border-strong': 'rgba(129, 140, 248, 0.48)',
            '--theme-accent-soft-hover': 'rgba(99, 102, 241, 0.18)'
        },
        ocean: {
            '--theme-bg': '#f0f9ff',
            '--theme-bg-subtle': '#f8fafc',
            '--theme-surface': '#ffffff',
            '--theme-surface-subtle': '#f0f9ff',
            '--theme-border': '#bae6fd',
            '--theme-border-strong': '#7dd3fc',
            '--theme-accent': '#0284c7',
            '--theme-accent-hover': '#0369a1',
            '--theme-accent-soft': '#e0f2fe',
            '--theme-accent-border': '#bae6fd',
            '--theme-accent-border-strong': '#7dd3fc',
            '--theme-accent-soft-hover': '#bae6fd'
        },
        purple: {
            '--theme-bg': '#faf5ff',
            '--theme-bg-subtle': '#fbf7ff',
            '--theme-surface': '#ffffff',
            '--theme-surface-subtle': '#f5f3ff',
            '--theme-border': '#ddd6fe',
            '--theme-border-strong': '#c4b5fd',
            '--theme-accent': '#7c3aed',
            '--theme-accent-hover': '#6d28d9',
            '--theme-accent-soft': '#f3e8ff',
            '--theme-accent-border': '#ddd6fe',
            '--theme-accent-border-strong': '#c4b5fd',
            '--theme-accent-soft-hover': '#ede9fe'
        },
        cyber: {
            '--theme-bg': '#f5fbff',
            '--theme-bg-subtle': '#eef8ff',
            '--theme-surface': '#ffffff',
            '--theme-surface-subtle': '#f0f9ff',
            '--theme-surface-muted': '#e0f2fe',
            '--theme-border': '#bae6fd',
            '--theme-border-strong': '#7dd3fc',
            '--theme-text-title': '#082f49',
            '--theme-text-card': '#0f172a',
            '--theme-text-body': '#334155',
            '--theme-text-secondary': '#64748b',
            '--theme-text-label': '#0369a1',
            '--theme-accent': '#22d3ee',
            '--theme-accent-hover': '#0891b2',
            '--theme-accent-soft': '#cffafe',
            '--theme-accent-border': '#a5f3fc',
            '--theme-accent-border-strong': '#67e8f9',
            '--theme-accent-soft-hover': '#a5f3fc'
        },
        classic: {
            '--theme-bg': '#ffffff',
            '--theme-bg-subtle': '#f8fafc',
            '--theme-surface': '#ffffff',
            '--theme-surface-subtle': '#f8fafc',
            '--theme-surface-muted': '#f1f5f9',
            '--theme-border': '#d7dee8',
            '--theme-border-strong': '#b6c2d1',
            '--theme-accent': '#2563eb',
            '--theme-accent-hover': '#1d4ed8',
            '--theme-accent-soft': '#eff6ff',
            '--theme-accent-border': '#bfdbfe',
            '--theme-accent-border-strong': '#93c5fd',
            '--theme-accent-soft-hover': '#dbeafe'
        }
    },
    writeStorage: function(theme) {
        this.active = theme;
    },
    current: function() {
        return this.themes.some(function(item) { return item[0] === this.active; }, this) ? this.active : this.fallback;
    },
    apply: function(name) {
        var theme = this.themes.some(function(item) { return item[0] === name; }) ? name : this.fallback;
        document.documentElement.setAttribute('data-aicf-theme', theme);
        if (document.body) document.body.setAttribute('data-aicf-theme', theme);
        Object.entries(this.tokens[theme] || {}).forEach(function(entry) {
            document.documentElement.style.setProperty(entry[0], entry[1]);
        });
        var aliases = {
            '--bg': '--theme-bg',
            '--bg-subtle': '--theme-bg-subtle',
            '--surface': '--theme-surface',
            '--surface-subtle': '--theme-surface-subtle',
            '--surface-muted': '--theme-surface-muted',
            '--border': '--theme-border',
            '--border-strong': '--theme-border-strong',
            '--text-title': '--theme-text-title',
            '--text-card': '--theme-text-card',
            '--text-body': '--theme-text-body',
            '--text-secondary': '--theme-text-secondary',
            '--text-label': '--theme-text-label',
            '--accent': '--theme-accent',
            '--accent-foreground': '--theme-accent-foreground',
            '--accent-hover': '--theme-accent-hover',
            '--accent-soft': '--theme-accent-soft',
            '--accent-border': '--theme-accent-border',
            '--accent-border-strong': '--theme-accent-border-strong',
            '--accent-soft-hover': '--theme-accent-soft-hover',
            '--success': '--theme-success',
            '--success-text': '--theme-success-text',
            '--success-soft': '--theme-success-soft',
            '--warning': '--theme-warning',
            '--warning-text': '--theme-warning-text',
            '--warning-soft': '--theme-warning-soft',
            '--danger': '--theme-danger',
            '--danger-text': '--theme-danger-text',
            '--danger-soft': '--theme-danger-soft',
            '--focus': '--theme-focus',
            '--scrollbar-thumb': '--theme-scrollbar-thumb',
            '--overlay-scrim': '--theme-overlay-scrim',
            '--preview-scrim': '--theme-preview-scrim',
            '--active-ring': '--theme-active-ring',
            '--danger-ring': '--theme-danger-ring',
            '--shadow': '--theme-shadow',
            '--shadow-hover': '--theme-shadow-hover',
            '--shadow-overlay': '--theme-shadow-overlay'
        };
        Object.entries(aliases).forEach(function(entry) {
            var value = getComputedStyle(document.documentElement).getPropertyValue(entry[1]).trim();
            if (value) document.documentElement.style.setProperty(entry[0], value);
        });
        this.writeStorage(theme);
        this.syncButtons(theme);
    },
    syncButtons: function(theme) {
        document.querySelectorAll('.theme-choice').forEach(function(button) {
            var active = button.dataset.theme === theme;
            button.classList.toggle('active', active);
            button.setAttribute('aria-pressed', active ? 'true' : 'false');
        });
        document.querySelectorAll('.theme-choice-input').forEach(function(input) {
            var active = input.value === theme;
            input.checked = active;
            if (active) {
                input.setAttribute('checked', 'checked');
            } else {
                input.removeAttribute('checked');
            }
        });
    },
    ensureSynced: function() {
        var theme = this.current();
        this.apply(theme);
        this.syncButtons(theme);
    },
    observeSwitcher: function() {
        if (this._observerBound) return;
        this._observerBound = true;
        var self = this;
        var syncSoon = function() {
            clearTimeout(self._syncTimer);
            self._syncTimer = setTimeout(function() {
                self.syncButtons(self.current());
            }, 60);
        };
        if (document.body && window.MutationObserver) {
            var observer = new MutationObserver(function(mutations) {
                for (var i = 0; i < mutations.length; i += 1) {
                    var target = mutations[i].target;
                    if ((target && target.closest && target.closest('.theme-switcher')) || document.querySelector('.theme-switcher')) {
                        syncSoon();
                        break;
                    }
                }
            });
            observer.observe(document.body, {childList: true, subtree: true, attributes: true, attributeFilter: ['style', 'class']});
        }
        document.addEventListener('visibilitychange', syncSoon);
        window.addEventListener('focus', syncSoon);
    },
    mount: function() {
        var drawer = document.getElementById('settings-panel') || document.getElementById('dev-drawer');
        if (!drawer) return;
        if (drawer.querySelector('.theme-switcher')) {
            this.syncButtons(this.current());
            return;
        }
        var tabs = drawer.querySelector('.dev-tabs');
        var panel = document.createElement('div');
        panel.className = 'theme-switcher';
        panel.innerHTML =
            '<div class="theme-switcher-title">' +
            '<span>Appearance</span><span class="theme-switcher-caption">Token themes</span>' +
            '</div><div class="theme-switcher-grid"></div>';
        var grid = panel.querySelector('.theme-switcher-grid');
        this.themes.forEach(function(item) {
            var button = document.createElement('button');
            button.type = 'button';
            button.className = 'theme-choice';
            button.dataset.theme = item[0];
            button.textContent = item[1];
            button.addEventListener('click', function() {
                window.AICF.theme.apply(item[0]);
            });
            grid.appendChild(button);
        });
        drawer.insertBefore(panel, tabs || drawer.firstChild);
        this.syncButtons(this.current());
    }
};
window.AICF.theme.ensureSynced();
window.AICF.theme.observeSwitcher();
[50, 200, 600, 1200].forEach(function(delay) {
    setTimeout(function() {
        if (window.AICF && window.AICF.theme) window.AICF.theme.ensureSynced();
    }, delay);
});
window.AICF.setPanelVisible = function(id, visible) {
    var panel = document.getElementById(id);
    if (!panel) return;
    if (visible) {
        if (panel.hidden) panel.hidden = false;
        if (panel.hasAttribute('hidden')) panel.removeAttribute('hidden');
        if (panel.style.display === 'none' || getComputedStyle(panel).display === 'none') panel.style.display = 'block';
    } else {
        if (!panel.hidden) panel.hidden = true;
        if (!panel.hasAttribute('hidden')) panel.setAttribute('hidden', '');
        if (panel.style.display !== 'none') panel.style.display = 'none';
    }
};
window.AICF.readRadioValue = function(rootId, fallback) {
    var root = document.getElementById(rootId);
    if (!root) return fallback;
    var checked = root.querySelector('input[type="radio"]:checked');
    if (checked && checked.value) return checked.value;
    return fallback;
};
window.AICF.radioValueFromEventTarget = function(target) {
    if (!target) return '';
    if (target.matches && target.matches('input[type="radio"]')) return target.value || '';
    var input = target.querySelector ? target.querySelector('input[type="radio"]') : null;
    if (input && input.value) return input.value;
    var label = target.closest ? target.closest('label') : null;
    if (label) {
        input = label.querySelector ? label.querySelector('input[type="radio"]') : null;
        if (input && input.value) return input.value;
        var text = (label.textContent || '').trim();
        if (text) return text;
    }
    return '';
};
window.AICF.openAssetManagement = function(open) {
    window.__AICF_ASSET_MANAGEMENT_OPEN__ = open;
    window.AICF.syncPageTabs();
};
document.addEventListener('click', function(event) {
    var target=event.target;
    if(!target || !target.closest) return;
    if(target.closest('#project_assets_open_btn')) window.AICF.openAssetManagement(true);
    if(target.closest('#close_project_assets_btn')) window.AICF.openAssetManagement(false);
}, true);
document.addEventListener('change', function(event) {
    if(event.target.closest && event.target.closest('#main_page_tabs')) {
        window.__AICF_ASSET_MANAGEMENT_OPEN__=false;
        window.AICF.syncPageTabs();
    }
}, true);
window.AICF.syncPageTabs = function() {
    var mainMap = {
        'Workbench': 'workbench',
        'Assets': 'assets',
        '素材管理': 'assets',
        '素材库': 'assets',
        '创作工作台': 'workbench',
        '设置': 'settings',
        'Settings': 'settings',
        'workbench': 'workbench',
        'assets': 'assets',
        'settings': 'settings'
    };
    var mainPage = window.AICF.readRadioValue('main_page_tabs', window.__AICF_MAIN_PAGE__ || 'workbench');
    mainPage = mainMap[mainPage] || 'workbench';
    window.__AICF_MAIN_PAGE__ = mainPage;
    var assetsOpen=!!window.__AICF_ASSET_MANAGEMENT_OPEN__;
    window.AICF.setPanelVisible('workbench_page', !assetsOpen && mainPage === 'workbench');
    window.AICF.setPanelVisible('asset_library_page', assetsOpen);
    window.AICF.setPanelVisible('settings_page', !assetsOpen && mainPage === 'settings');

    var settingsMap = {
        'Model': 'model',
        'Workflow': 'workflow',
        'Developer': 'developer',
        'model': 'model',
        'workflow': 'workflow',
        'developer': 'developer'
    };
    var settingsPage = window.AICF.readRadioValue('settings_subpage_tabs', window.__AICF_SETTINGS_PAGE__ || 'model');
    settingsPage = settingsMap[settingsPage] || 'model';
    window.__AICF_SETTINGS_PAGE__ = settingsPage;
    window.AICF.setPanelVisible('dev-tab1', settingsPage === 'model');
    window.AICF.setPanelVisible('dev-tab2', settingsPage === 'workflow');
    window.AICF.setPanelVisible('dev-tab3', settingsPage === 'developer');
};
window.AICF.schedulePageTabSync = function() {
    [0, 40, 120, 260].forEach(function(delay) {
        setTimeout(function() {
            if (window.AICF && window.AICF.syncPageTabs) window.AICF.syncPageTabs();
        }, delay);
    });
};
window.AICF.observePageTabs = function() {
    if (window.__AICF_PAGE_TABS_OBSERVED__ || !window.MutationObserver || !document.body) return;
    window.__AICF_PAGE_TABS_OBSERVED__ = true;
    var timer = null;
    var observer = new MutationObserver(function(mutations) {
        var shouldSync = false;
        for (var i = 0; i < mutations.length; i += 1) {
            var target = mutations[i].target;
            if (!target || !target.closest) continue;
            if (
                target.closest('#main_pages') ||
                target.closest('#main_page_tabs') ||
                target.closest('#settings_subpage_tabs') ||
                target.closest('#settings_page')
            ) {
                shouldSync = true;
                break;
            }
        }
        if (!shouldSync) return;
        clearTimeout(timer);
        timer = setTimeout(function() {
            if (window.AICF && window.AICF.syncPageTabs) window.AICF.syncPageTabs();
        }, 80);
    });
    observer.observe(document.body, {
        childList: true,
        subtree: true,
        attributes: true,
        attributeFilter: ['style', 'hidden', 'class']
    });
};
window.AICF.disablePausePolling = function() {
    if (window.__AICF_PAUSE_POLL_INTERVAL__) {
        clearInterval(window.__AICF_PAUSE_POLL_INTERVAL__);
        window.__AICF_PAUSE_POLL_INTERVAL__ = null;
    }
    var button = document.querySelector('.pipeline-pause-btn');
    if (button) button.style.display = 'none';
    if (!window.__AICF_PAUSE_API_404_WARNED__) {
        window.__AICF_PAUSE_API_404_WARNED__ = true;
        console.warn('one-click pause API routes are unavailable; polling stopped');
    }
};
window.AICF.toggleOneClickPause = async function(button) {
    if (!button || button.disabled) return;
    var paused = button.dataset.paused === 'true';
    button.disabled = true;
    try {
        var response = await fetch(paused ? '/api/one_click/continue' : '/api/one_click/pause', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'}
        });
        if (response.status === 404) {
            window.AICF.disablePausePolling();
            return;
        }
        await response.json();
    } catch (error) {
        console.warn('pause control failed', error);
    } finally {
        button.disabled = false;
    }
};
window.AICF.pollOneClickStatus = async function() {
    try {
        var response = await fetch('/api/one_click/status', {cache: 'no-store'});
        if (response.status === 404) {
            window.AICF.disablePausePolling();
            return;
        }
        if (!response.ok) return;
        var data = await response.json();
        var button = document.querySelector('.pipeline-pause-btn');
        if (button) {
            button.style.display = data.running ? 'inline-flex' : 'none';
            button.dataset.paused = data.paused ? 'true' : 'false';
            button.textContent = data.paused
                ? '\u25b6\u7ee7\u7eed'
                : (data.pause_requested ? '\u23f3\u6682\u505c\u4e2d' : '\u23f8\u6682\u505c');
        }
        var statusStrong = document.querySelector('.pipeline-meta span:first-child strong');
        if (statusStrong && data.paused) statusStrong.textContent = '\u6682\u505c\u4e2d';
    } catch (error) {}
};
if (!window.__AICF_PAUSE_POLL_INTERVAL__) {
    window.__AICF_PAUSE_POLL_INTERVAL__ = setInterval(function() {
        window.AICF.pollOneClickStatus();
    }, 500);
}
window.AICF.pollOneClickStatus();
window.AICF.toggleDevDrawer = function() {
    window.toggleDevDrawer();
};
window.AICF.closeDevDrawer = function() {
    var overlay = document.getElementById('dev-overlay');
    var drawer = document.getElementById('settings-panel') || document.getElementById('dev-drawer');
    if (overlay) overlay.classList.remove('open');
    if (drawer) drawer.classList.remove('open');
};
window.AICF.switchDevTab = function(evt, tabId) {
    window.switchDevTab(evt, tabId);
};
window.AICF.bindTopActionButtons = function() {
    var drawerToggle = document.getElementById('dev-drawer-toggle');
    if (drawerToggle && !drawerToggle._aicfBound) {
        drawerToggle._aicfBound = true;
        drawerToggle.addEventListener('click', function(evt) {
            evt.preventDefault();
            evt.stopPropagation();
            window.toggleDevDrawer();
        });
    }
    document.querySelectorAll('.preview-final-btn').forEach(function(btn) {
        if (btn._aicfBound) return;
        btn._aicfBound = true;
        btn.addEventListener('click', function(evt) {
            evt.preventDefault();
            evt.stopPropagation();
            window.previewLatestFinalVideo();
        });
    });
    document.querySelectorAll('.open-videos-dir-btn').forEach(function(btn) {
        if (btn._aicfBound) return;
        btn._aicfBound = true;
        btn.addEventListener('click', function(evt) {
            evt.preventDefault();
            evt.stopPropagation();
            window.openCurrentVideosDir();
        });
    });
    var archiveHost = document.getElementById('archive_generation_btn');
    var archiveButton = archiveHost && archiveHost.tagName === 'BUTTON'
        ? archiveHost
        : archiveHost ? archiveHost.querySelector('button') : null;
    if (archiveButton && !archiveButton._aicfH3SaveGuardBound) {
        archiveButton._aicfH3SaveGuardBound = true;
        archiveButton.addEventListener('click', function(evt) {
            if (archiveButton._aicfArchiveBypass) return;
            var modal = document.getElementById('aicf-prompt-modal');
            var pending = window.AICF.h3AutosavePending || null;
            if (modal && modal._h3AutosaveTimer) {
                window.clearTimeout(modal._h3AutosaveTimer);
                modal._h3AutosaveTimer = null;
                var segmentIndex = String((modal.dataset && modal.dataset.segmentIndex) || '');
                if (segmentIndex) pending = window.AICF.saveH3GenerationConfig(segmentIndex, null, modal);
            }
            if (!pending) return;
            evt.preventDefault();
            evt.stopImmediatePropagation();
            archiveButton.disabled = true;
            Promise.resolve(pending).then(function() {
                archiveButton.disabled = false;
                archiveButton._aicfArchiveBypass = true;
                archiveButton.click();
                archiveButton._aicfArchiveBypass = false;
            }).catch(function(error) {
                archiveButton.disabled = false;
                window.alert(error && error.message ? error.message : 'H3 参考绑定保存失败，归档已取消。');
            });
        }, true);
    }
};
window.AICF.bindDevDrawerInteractions = function() {
    if (window.__AICF_DEV_DRAWER_BOUND__) return;
    window.__AICF_DEV_DRAWER_BOUND__ = true;
    document.addEventListener('click', function(evt) {
        var drawerToggle = evt.target.closest ? evt.target.closest('#dev-drawer-toggle, .gear-icon') : null;
        if (drawerToggle) {
            evt.preventDefault();
            window.toggleDevDrawer();
            return;
        }
        var themeButton = evt.target.closest ? evt.target.closest('.theme-choice') : null;
        if (themeButton && themeButton.dataset && themeButton.dataset.theme && window.AICF && window.AICF.theme) {
            evt.preventDefault();
            evt.stopPropagation();
            var input = document.getElementById(themeButton.getAttribute('for') || '');
            if (input) {
                input.checked = true;
                input.setAttribute('checked', 'checked');
            }
            window.AICF.theme.apply(themeButton.dataset.theme);
            return;
        }
        var themeInput = evt.target.closest ? evt.target.closest('.theme-choice-input') : null;
        if (themeInput && themeInput.value && window.AICF && window.AICF.theme) {
            window.AICF.theme.apply(themeInput.value);
            return;
        }
        var mainPageNav = evt.target.closest ? evt.target.closest('#main_page_tabs') : null;
        if (mainPageNav && window.AICF) {
            var mainTarget = window.AICF.radioValueFromEventTarget(evt.target);
            var mainMap = {'Workbench': 'workbench', 'Assets': 'assets', 'Settings': 'settings', 'workbench': 'workbench', 'assets': 'assets', 'settings': 'settings'};
            if (mainMap[mainTarget]) window.__AICF_MAIN_PAGE__ = mainMap[mainTarget];
            window.AICF.schedulePageTabSync();
            if (window.AICF.theme) {
                [80, 250, 600].forEach(function(delay) {
                    setTimeout(function() {
                        window.AICF.theme.ensureSynced();
                    }, delay);
                });
            }
        }
        var settingsPageNav = evt.target.closest ? evt.target.closest('#settings_subpage_tabs') : null;
        if (settingsPageNav && window.AICF) {
            var settingsTarget = window.AICF.radioValueFromEventTarget(evt.target);
            var settingsMap = {'Model': 'model', 'Workflow': 'workflow', 'Developer': 'developer', 'model': 'model', 'workflow': 'workflow', 'developer': 'developer'};
            if (settingsMap[settingsTarget]) window.__AICF_SETTINGS_PAGE__ = settingsMap[settingsTarget];
            window.AICF.schedulePageTabSync();
        }
        var tabButton = evt.target.closest ? evt.target.closest('.dev-tab-btn[data-dev-tab]') : null;
        if (tabButton) {
            evt.preventDefault();
            evt.stopPropagation();
            window.AICF.switchDevTab({currentTarget: tabButton}, tabButton.dataset.devTab);
            return;
        }
    }, true);
    document.addEventListener('change', function(evt) {
        var target = evt.target;
        if (!target || !target.closest) return;
        if (target.closest('#main_page_tabs') || target.closest('#settings_subpage_tabs')) {
            var changedValue = window.AICF.radioValueFromEventTarget(target);
            var changedMainMap = {'Workbench': 'workbench', 'Assets': 'assets', 'Settings': 'settings', 'workbench': 'workbench', 'assets': 'assets', 'settings': 'settings'};
            var changedSettingsMap = {'Model': 'model', 'Workflow': 'workflow', 'Developer': 'developer', 'model': 'model', 'workflow': 'workflow', 'developer': 'developer'};
            if (target.closest('#main_page_tabs') && changedMainMap[changedValue]) window.__AICF_MAIN_PAGE__ = changedMainMap[changedValue];
            if (target.closest('#settings_subpage_tabs') && changedSettingsMap[changedValue]) window.__AICF_SETTINGS_PAGE__ = changedSettingsMap[changedValue];
            if (window.AICF && window.AICF.schedulePageTabSync) window.AICF.schedulePageTabSync();
        }
    }, true);
};
window.AICF.escapeHtml = function(value) {
    return String(value || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
};
window.AICF.stripQualityAnnotations = function(value) {
    return String(value || '')
        .replace(/^\s*\[Risk\s*\d+\].*$/gm, '')
        .replace(/\*\*([^*]+)\*\*/g, '$1')
        .replace(/\n{3,}/g, '\n\n')
        .trim();
};
window.AICF.currentQualityIssues = function(value) {
    return [];
};
window.AICF.renderQualityPreview = function(textarea, overlay, note) {
    if (!textarea || !overlay) return;
    var raw = textarea.value || '';
    var clean = window.AICF.stripQualityAnnotations(raw);
    if (raw !== clean && !textarea._aicfCleaningQualityText) {
        textarea._aicfCleaningQualityText = true;
        textarea.value = clean;
        textarea.dispatchEvent(new Event('input', {bubbles: true}));
        textarea._aicfCleaningQualityText = false;
        raw = clean;
    }
    var issues = window.AICF.currentQualityIssues(raw);
    var hasRisk = issues.length > 0;
    // Keep the native textarea visible and editable. Risk highlighting is shown
    // in a separate preview below the editor instead of being overlaid on top
    // of the textarea, so the caret always belongs to the real text layer.
    textarea.classList.remove('quality-overlay-active');
    if (note) note.style.display = hasRisk ? 'block' : 'none';
    overlay.style.display = hasRisk ? 'block' : 'none';
    if (!hasRisk) {
        overlay.innerHTML = '';
        return;
    }
    var phrases = issues.map(function(issue) { return issue.phrase; });
    phrases = Array.from(new Set(phrases.filter(Boolean))).sort(function(a, b) { return b.length - a.length; });
    var html = window.AICF.escapeHtml(clean);
    phrases.forEach(function(phrase) {
        var escaped = window.AICF.escapeHtml(phrase);
        if (!escaped || html.indexOf('quality-risk-hit">' + escaped + '</mark>') >= 0) return;
        html = html.replace(new RegExp(escaped.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'g'), '<mark class="quality-risk-hit">' + escaped + '</mark>');
    });
    var notes = issues.map(function(issue, index) {
        return '<div class="quality-risk-note-item"><strong>椋庨櫓' + (index + 1) + '</strong><span class="quality-risk-phrase">' + window.AICF.escapeHtml(issue.phrase) + '</span><span class="quality-risk-reason">' + window.AICF.escapeHtml(issue.reason) + '</span></div>';
    }).join('');
    overlay.innerHTML = '<div class="quality-preview-title">椋庨櫓浣嶇疆棰勮</div><div class="quality-preview-body">' + html.replace(/\n/g, '<br>') + '</div>';
    if (note) {
        var badge = note.querySelector('.quality-risk-badge');
        var detail = note.querySelector('.quality-risk-detail');
        var count = issues.length;
        note.style.display = hasRisk ? 'block' : 'none';
        if (badge) {
            badge.innerHTML = '<span class="quality-risk-icon">鈿?/span><span>' + count + ' 鏉￠闄?/span><span class="quality-risk-chevron">灞曞紑</span>';
            badge.setAttribute('aria-expanded', note.classList.contains('is-expanded') ? 'true' : 'false');
        }
        if (detail) detail.innerHTML = notes;
    }
    overlay.scrollTop = 0;
    overlay.scrollLeft = 0;
};
window.AICF.bindQualityHighlightPreviews = function() {
    ['story_editor', 'beats_editor'].forEach(function(id) {
        var editor = document.getElementById(id);
        var textarea = editor ? editor.querySelector('textarea') : null;
        if (!editor || !textarea || textarea._aicfQualityPreviewBound) return;
        textarea._aicfQualityPreviewBound = true;
        var wrap = textarea.parentElement || editor;
        wrap.classList.add('quality-editor-shell');

        var header = document.createElement('div');
        header.className = 'quality-editor-header';
        var title = document.createElement('div');
        title.className = 'quality-editor-title';
        title.textContent = id === 'story_editor' ? 'Story' : 'Beats';
        var note = document.createElement('div');
        note.className = 'quality-risk-summary';
        note.setAttribute('aria-live', 'polite');
        note.innerHTML = '<button type="button" class="quality-risk-badge" aria-expanded="false"></button><div class="quality-risk-detail"></div>';
        header.appendChild(title);
        header.appendChild(note);

        var body = wrap;
        body.className += ' quality-editor-body quality-highlight-host';
        wrap.insertBefore(header, textarea);
        body.dataset.aicfEditorBody = 'true';
        

        var overlay = document.createElement('div');
        overlay.className = 'quality-highlight-overlay';
        overlay.setAttribute('aria-hidden', 'true');
        body.appendChild(overlay);

        var badge = note.querySelector('.quality-risk-badge');
        if (badge) {
            badge.addEventListener('click', function() {
                note.classList.toggle('is-expanded');
                badge.setAttribute('aria-expanded', note.classList.contains('is-expanded') ? 'true' : 'false');
            });
        }
        var render = function() { window.AICF.renderQualityPreview(textarea, overlay, note); };
        textarea.addEventListener('input', render);
        textarea.addEventListener('change', render);
        textarea.addEventListener('scroll', function() {
            overlay.scrollTop = textarea.scrollTop;
            overlay.scrollLeft = textarea.scrollLeft;
        });
        render();
    });
    if (!window.__AICF_QUALITY_PREVIEW_INTERVAL__) {
        window.__AICF_QUALITY_PREVIEW_INTERVAL__ = setInterval(function() {
            document.querySelectorAll('#story_editor textarea, #beats_editor textarea').forEach(function(textarea) {
                if (textarea._aicfQualityLastValue === textarea.value) return;
                textarea._aicfQualityLastValue = textarea.value;
                var editor = textarea.closest ? textarea.closest('#story_editor, #beats_editor') : null;
                var overlay = editor ? editor.querySelector('.quality-highlight-overlay') : null;
                var note = editor ? editor.querySelector('.quality-risk-summary') : null;
                if (overlay) {
                    window.AICF.renderQualityPreview(textarea, overlay, note);
                }
            });
        }, 800);
    }
};
// Legacy bare-name aliases are defined before initAICF() so inline handlers work
// even if a later desktop WebView-specific script branch fails.

// ---- Gradio hidden-component helpers ----
window._findGradioInput = function(id) {
    var el = document.getElementById(id);
    if (!el) return null;
    if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') return el;
    return el.querySelector('textarea') || el.querySelector('input');
};
window._findGradioBtn = function(id) {
    var el = document.getElementById(id);
    if (!el) return null;
    if (el.tagName === 'BUTTON') return el;
    return el.querySelector('button');
};

// ---- Gradio hidden-trigger functions (AICF namespace) ----
window.AICF.triggerKeyImgRegen = function(payload) {
    var box = window._findGradioInput('key_img_regen_payload');
    if (!box) { console.warn('triggerKeyImgRegen: input not found'); return; }
    box.value = payload;
    box.dispatchEvent(new Event('input', {bubbles: true}));
    var btn = window._findGradioBtn('key_img_regen_btn');
    if (!btn) { console.warn('triggerKeyImgRegen: button not found'); return; }
    btn.click();
};
window.AICF.triggerPartKeyImgRegen = function(payload) {
    var box = window._findGradioInput('key_img_part_payload');
    if (!box) { console.warn('triggerPartKeyImgRegen: input not found'); return; }
    box.value = payload;
    box.dispatchEvent(new Event('input', {bubbles: true}));
    var btn = window._findGradioBtn('key_img_part_btn');
    if (!btn) { console.warn('triggerPartKeyImgRegen: button not found'); return; }
    btn.click();
};
// ---- AICF: stable wrappers around Gradio hidden DOM helpers ----
window.AICF.findGradioBtn = function(id) {
    var el = document.getElementById(id);
    if (!el) return null;
    if (el.tagName === 'BUTTON') return el;
    return el.querySelector('button');
};
window.AICF.findGradioInput = function(id) {
    var el = document.getElementById(id);
    if (!el) return null;
    if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') return el;
    return el.querySelector('textarea') || el.querySelector('input');
};
if (!window.AICF._beatRiskSelectionBound) {
    window.AICF._beatRiskSelectionBound = true;
    document.addEventListener('click', function(event) {
        var button = event.target && event.target.closest ? event.target.closest('.beat-risk-select[data-beat-index]') : null;
        if (!button) return;
        event.preventDefault();
        var input = window.AICF.findGradioInput('complex_beat_index');
        if (!input) return;
        input.value = String(button.dataset.beatIndex || '');
        input.dispatchEvent(new Event('input', {bubbles: true}));
        input.dispatchEvent(new Event('change', {bubbles: true}));
        input.focus();
    }, true);
}
window.AICF.triggerPartVideoRegen = function(btnId) {
    var btn = window.AICF.findGradioBtn(btnId);
    if (!btn) { console.warn('triggerPartVideoRegen: button not found:', btnId); return; }
    btn.click();
};
window.AICF.triggerSegmentRegen = function(segmentIndex, triggerButton) {
    if (window.AICF.archiveReadOnly) return false;
    var box = window.AICF.findGradioInput('segment_regen_payload');
    if (!box) { console.warn('triggerSegmentRegen: input not found'); return false; }
    var btn = window.AICF.findGradioBtn('segment_regen_btn');
    if (!btn) { console.warn('triggerSegmentRegen: button not found'); return false; }
    var indexText = String(segmentIndex || '');
    var originalText = triggerButton ? triggerButton.textContent : '';
    if (triggerButton) { triggerButton.disabled = true; triggerButton.textContent = '准备重生成…'; }
    var modal = document.getElementById('aicf-prompt-modal');
    var modalMatches = modal && String((modal.dataset && modal.dataset.segmentIndex) || '') === indexText;
    var pending = window.AICF.h3AutosavePending || null;
    if (modalMatches && modal._h3AutosaveTimer) {
        window.clearTimeout(modal._h3AutosaveTimer);
        modal._h3AutosaveTimer = null;
        pending = window.AICF.saveH3GenerationConfig(indexText, null, modal);
    }
    Promise.resolve(pending).catch(function(error) {
        throw error;
    }).then(function() {
        box.value = indexText;
        box.dispatchEvent(new Event('input', {bubbles: true}));
        box.dispatchEvent(new Event('change', {bubbles: true}));
        window.setTimeout(function() { btn.click(); }, 80);
    }).catch(function(error) {
        alert(error && error.message ? error.message : '参考素材尚未保存，无法重新生成提示词');
    }).finally(function() {
        if (triggerButton) { triggerButton.disabled = false; triggerButton.textContent = originalText || '重新生成提示词'; }
    });
    return false;
};
window.AICF.retryBeatSegment = function(index, button) {
    if (window.AICF.archiveReadOnly) return false;
    const card = button.closest('.group-card');
    button.disabled = true;
    fetch('/api/web-llm/beat-segment/retry', {method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({segment_index:index, batch_id:button.dataset.batchId, runtime:card.dataset.runtime})})
      .then(r=>r.json()).then(data=>{if(!data.ok)throw new Error(data.error||data.message||'重试失败');})
      .catch(error=>alert(error.message)).finally(()=>{button.disabled=false;});
    return false;
};
window.AICF.playUsageAttemptVideo = function(row) {
    var url = row && row.dataset ? row.dataset.videoUrl : '';
    if (!url) {
        alert('该任务的视频尚未生成，或已被删除。');
        return false;
    }
    window.AICFPreview.video(url);
    return false;
};
window.AICF.openUsageAttemptPrompt = function(button) {
    var prompt = button && button.dataset ? button.dataset.submittedPrompt : '';
    if (!prompt) {
        alert('这条旧任务记录没有保存提交提示词。');
        return false;
    }
    var overlay = document.getElementById('usage-attempt-prompt-overlay');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'usage-attempt-prompt-overlay';
        overlay.className = 'usage-attempt-prompt-overlay';
        overlay.innerHTML =
            '<section class="usage-attempt-prompt-dialog" role="dialog" aria-modal="true" aria-label="本次提交提示词">' +
            '<div class="usage-attempt-prompt-head"><strong>本次提交提示词</strong>' +
            '<button type="button" class="usage-attempt-prompt-close" aria-label="关闭">&#10005;</button></div>' +
            '<pre class="usage-attempt-prompt-body"></pre></section>';
        overlay.addEventListener('click', function(event) {
            if (event.target === overlay) overlay.classList.remove('open');
        });
        overlay.querySelector('.usage-attempt-prompt-close').addEventListener('click', function() {
            overlay.classList.remove('open');
        });
        document.body.appendChild(overlay);
    }
    var body = overlay.querySelector('.usage-attempt-prompt-body');
    if (body) body.textContent = prompt;
    overlay.classList.add('open');
    var closeButton = overlay.querySelector('.usage-attempt-prompt-close');
    if (closeButton) closeButton.focus();
    return false;
};
if (!window.AICF._usagePromptEscapeBound) {
    window.AICF._usagePromptEscapeBound = true;
    document.addEventListener('keydown', function(event) {
        if (event.key === 'Escape') {
            var overlay = document.getElementById('usage-attempt-prompt-overlay');
            if (overlay) overlay.classList.remove('open');
        }
    });
}
window.AICF.confirmDeleteTaskVideo = function(segmentIndex, taskId, button) {
    if (window.AICF.archiveReadOnly || !taskId || (button && button.disabled)) return false;
    var confirmed = window.confirm(
        '确定删除任务 ' + taskId + ' 生成的视频吗？\n\n' +
        '此操作不可恢复；任务 ID、计费信息和本次提交提示词会保留。'
    );
    if (!confirmed) return false;
    var originalText = button ? button.textContent : '';
    if (button) {
        button.disabled = true;
        button.textContent = '…';
    }
    fetch('/api/segment/delete_task_video', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({segment_index: Number(segmentIndex), task_id: String(taskId)})
    }).then(function(response) {
        return response.json().then(function(data) {
            if (!response.ok || !data || !data.ok) {
                throw new Error((data && data.message) || '删除任务视频失败');
            }
            window.location.reload();
        });
    }).catch(function(error) {
        console.error('delete task video failed', error);
        alert(error && error.message ? error.message : '删除任务视频失败');
        if (button) {
            button.disabled = false;
            button.textContent = originalText || '×';
        }
    });
    return false;
};
window.AICF.resetQueuedSegment = function(segmentIndex, button) {
    if (window.AICF.archiveReadOnly || (button && button.disabled)) return false;
    if (!window.confirm(
        '确定重置本段的排队状态吗？\n\n' +
        '原网页任务将被取消且不再接收结果；不会自动重发，也不会产生新的费用。\n' +
        '重置后可以重新提交该分段。'
    )) return false;
    var originalText = button ? button.textContent : '';
    var card = button && button.closest ? button.closest('.group-card') : null;
    var segmentUid = card ? (card.getAttribute('data-segment-uid') || '') : '';
    if (button) {
        button.disabled = true;
        button.textContent = '…';
    }
    fetch('/api/segment/reset_queued_task', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({segment_index: Number(segmentIndex), segment_uid: segmentUid})
    }).then(function(response) {
        return response.json().then(function(data) {
            if (!response.ok || !data || !data.ok) {
                throw new Error((data && data.message) || '重置排队任务失败');
            }
            window.location.reload();
        });
    }).catch(function(error) {
        console.error('reset queued segment failed', error);
        alert(error && error.message ? error.message : '重置排队任务失败');
        if (button) {
            button.disabled = false;
            button.textContent = originalText || '重置排队';
        }
    });
    return false;
};
window.AICF.selectUsageAttemptVideo = function(segmentIndex, taskId, button) {
    if (window.AICF.archiveReadOnly || !taskId || (button && button.disabled)) return false;
    var card = button && button.closest ? button.closest('.group-card') : null;
    if (button) {
        button.disabled = true;
        button.classList.add('is-loading');
        button.setAttribute('aria-busy', 'true');
    }
    fetch('/api/segment/select_task_video', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({segment_index: Number(segmentIndex), task_id: String(taskId)})
    }).then(function(response) {
        return response.json().then(function(data) {
            if (!response.ok || !data || !data.ok) {
                throw new Error((data && data.message) || '选择合成视频失败');
            }
            window.AICF.replaceSegmentCardFromResponse(segmentIndex, data, card, '.usage-select-btn.is-active');
        });
    }).catch(function(error) {
        console.error('select task video failed', error);
        alert(error && error.message ? error.message : '选择合成视频失败');
        if (button) {
            button.disabled = false;
            button.classList.remove('is-loading');
            button.removeAttribute('aria-busy');
        }
    });
    return false;
};
window.AICF.mountVideoChannels = async function(segmentIndex) {
    var modal = document.getElementById('aicf-prompt-modal');
    if (!modal || String(modal.dataset.segmentIndex) !== String(segmentIndex)) return;
    var channelSession = String(modal.dataset.h3Session || '');
    var pane = modal && modal.querySelector('.aicf-review-pane');
    if (!pane) return;
    var old = pane.querySelector('.video-channel-options');
    var previousChoice = old && old.querySelector('input:checked');
    var previousChannel = previousChoice ? previousChoice.value : '';
    if (old) old.remove();
    var field = document.createElement('fieldset');
    field.className = 'video-channel-options';
    var legend = document.createElement('legend'); legend.textContent = '视频生成渠道';
    field.appendChild(legend); pane.prepend(field);
    var hint = document.createElement('p'); hint.textContent = '正在读取渠道…'; field.appendChild(hint);
    try {
        var response = await fetch('/api/video-channels/segments/' + encodeURIComponent(segmentIndex));
        var data = await response.json();
        if (!response.ok || !data.ok) throw new Error(data.message || '无法读取视频渠道');
        if (String(modal.dataset.segmentIndex) !== String(segmentIndex) || String(modal.dataset.h3Session || '') !== channelSession || !field.isConnected) return;
        field._channels = data.channels || [];
        var browserLabel=document.createElement('label');browserLabel.textContent='网页视频执行浏览器';
        var browserSelect=document.createElement('select');browserSelect.setAttribute('aria-label','网页视频执行浏览器');
        var defaultBrowser=document.createElement('option');defaultBrowser.value='';defaultBrowser.textContent='跟随渠道默认 / 文字主浏览器';browserSelect.append(defaultBrowser);
        for(const browser of (data.browsers || []).filter(item=>item.online)) {
            var browserOption=document.createElement('option');browserOption.value=browser.id;
            browserOption.textContent=browser.name+(browser.online?' · 在线':' · 离线');browserSelect.append(browserOption);
        }
        browserSelect.value=data.video_browser_id || '';browserSelect.disabled=!!window.AICF.archiveReadOnly;
        browserSelect.onchange=function() {
            field.disabled=true;
            field._savePending=(async()=>{
                const response=await fetch('/api/video-channels/segments/'+encodeURIComponent(segmentIndex),{
                    method:'POST',headers:{'Content-Type':'application/json'},
                    body:JSON.stringify({channel_id:data.selected,video_browser_id:browserSelect.value})});
                const result=await response.json();if(!response.ok || !result.ok)throw new Error(result.message || '浏览器保存失败');
                data.video_browser_id=browserSelect.value;hint.textContent='执行浏览器已保存';
            })();
            field._savePending.catch(error=>{hint.textContent=error.message;browserSelect.value=data.video_browser_id || '';}).finally(()=>{field.disabled=false;});
        };
        browserLabel.append(browserSelect);field.append(browserLabel);
        if(data.video_task_id) {
            const move=document.createElement('button');move.type='button';move.textContent='将排队视频任务改到所选浏览器';
            move.disabled=!!window.AICF.archiveReadOnly;
            move.onclick=async()=>{
                move.disabled=true;
                try {
                    if(!browserSelect.value)throw new Error('请先选择具体浏览器');
                    const response=await fetch('/api/web-video/tasks/'+encodeURIComponent(data.video_task_id)+'/browser',{
                        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({client_id:browserSelect.value})});
                    const result=await response.json();if(!response.ok || !result.ok)throw new Error(result.message || '更换失败');
                    hint.textContent='排队视频任务已更换执行浏览器';
                }catch(error){hint.textContent=error.message;}finally{move.disabled=false;}
            };
            field.append(move);
        }
        hint.textContent = '默认使用 RunningHub；切换后按分段自动保存。';
        field._channels.forEach(function(item) {
            var label = document.createElement('label');
            label.style.cssText = 'display:block;padding:8px;margin:6px 0;border:1px solid #dce2ea;border-radius:6px';
            var input = document.createElement('input'); input.type = 'radio';
            input.name = 'editor-video-channel'; input.value = item.id;
            input.disabled = !item.available || !!window.AICF.archiveReadOnly;
            input.checked = item.id === (data.selected || 'runninghub');
            input.addEventListener('change', function() {
                if (!input.checked) return;
                hint.textContent = '正在保存渠道…';
                field.disabled = true;
                field._savePending = (async function() {
                    const savedResponse = await fetch('/api/video-channels/segments/' + encodeURIComponent(segmentIndex), {
                        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({channel_id:input.value})
                    });
                    const saved = await savedResponse.json();
                    if (!savedResponse.ok || !saved.ok) throw new Error(saved.message || '渠道保存失败');
                    data.selected = saved.selected;
                    if(field.isConnected)hint.textContent = '渠道已保存，后续重新生成将使用此渠道。';
                })();
                field._savePending.catch(function(error) {
                    if(field.isConnected) {
                        hint.textContent = error.message;
                        field.querySelectorAll('input').forEach(function(option) {option.checked=option.value===data.selected;});
                    }
                }).finally(function() {field.disabled = false;});
            });
            label.append(input, document.createTextNode(' ' + item.label));
            if (item.reason) { var reason = document.createElement('small'); reason.textContent = item.reason; reason.style.display = 'block'; label.appendChild(reason); }
            field.appendChild(label);
        });
    } catch (error) { hint.textContent = error.message || '无法读取视频渠道'; }
};
window.AICF.chooseVideoChannel = async function(segmentIndex) {
    var modal = document.getElementById('aicf-prompt-modal');
    var field = modal && String(modal.dataset.segmentIndex) === String(segmentIndex)
        ? modal.querySelector('.video-channel-options') : null;
    if(field && field._savePending)await field._savePending;
    var selected = field && field.querySelector('input:checked');
    var channel;
    if (selected) {
        channel = (field._channels || []).find(function(item) { return item.id === selected.value; });
    } else {
        // Card actions also work before the fullscreen picker has mounted.
        // The server owns saved selection and defaults missing values to RunningHub.
        var response = await fetch('/api/video-channels/segments/' + encodeURIComponent(segmentIndex));
        var data = await response.json();
        if (!response.ok || !data.ok) throw new Error(data.message || '无法读取视频渠道');
        var channelId = data.selected || 'runninghub';
        channel = (data.channels || []).find(function(item) { return item.id === channelId; });
    }
    if (!channel) throw new Error('已保存的视频渠道不存在，请检查渠道配置。');
    if (!channel.available) throw new Error(channel.reason || '当前视频渠道暂不可用');
    return channel;
};
window.AICF.watchWebVideoTask = function(segmentIndex, taskId) {
    if (!taskId) return;
    const started = Date.now();
    const check = async () => {
        try {
            const response = await fetch('/api/web-video/tasks/' + encodeURIComponent(taskId));
            const result = await response.json();
            if (response.ok && result.ok && ['complete','error','uncertain'].includes(result.task.state)) {
                const panelResponse = await fetch('/api/video-channels/segments/' + encodeURIComponent(segmentIndex) + '/panel');
                const panel = await panelResponse.json();
                if (panelResponse.ok && panel.ok) window.AICF.replaceSegmentCardFromResponse(segmentIndex, panel);
                return;
            }
        } catch (error) { console.warn('Video task status check failed', error); }
        if (Date.now() - started < 45 * 60 * 1000) setTimeout(check, 5000);
    };
    setTimeout(check, 5000);
};
window.AICF.triggerSegmentVideoRegen = async function(segmentIndex, button) {
    if (window.AICF.archiveReadOnly) return false;
    if (window.AICF.syncSegmentDirectorEditors) window.AICF.syncSegmentDirectorEditors();
    var textarea = window.AICF.segmentEditorFromButton
        ? window.AICF.segmentEditorFromButton(button, segmentIndex)
        : document.querySelector('.segment-director-editor[data-segment-index="' + String(segmentIndex || '') + '"]');
    var materialData = {reference_roles: [], reference_image_ids: {}, scene_id: ''};
    if (textarea && textarea.closest) {
        var card = textarea.closest('.group-card');
        var source = card ? card.querySelector('.segment-material-source') : null;
        if (source && source.dataset.materials) {
            try {
                var parsed = JSON.parse(source.dataset.materials);
                if (parsed && typeof parsed === 'object') materialData = parsed;
            } catch (e) {
                console.warn('triggerSegmentVideoRegen: invalid material payload', e);
            }
        }
    }
    var payload = {
        segment_index: String(segmentIndex || ''),
        director_prompt: textarea ? (textarea.value || '') : '',
        reference_roles: Array.isArray(materialData.reference_roles) ? materialData.reference_roles.slice(0, 4) : [],
        reference_image_ids: materialData.reference_image_ids && typeof materialData.reference_image_ids === 'object'
            ? Object.assign({}, materialData.reference_image_ids)
            : {},
        scene_id: typeof materialData.scene_id === 'string' ? materialData.scene_id : ''
    };
    var card = textarea && textarea.closest ? textarea.closest('.group-card') : null;
    button = button || (card ? card.querySelector('.primary-mini') : null);
    var oldText = button ? button.textContent : '';
    let channel;
    try { channel = await window.AICF.chooseVideoChannel(segmentIndex); }
    catch (error) { alert(error.message || '无法选择视频渠道'); return false; }
    if (!channel) return false;
    payload.channel_id = channel.id;
    if (button) {
        button.disabled = true;
        button.textContent = 'Submitting...';
    }
    fetch('/api/segment/regenerate_video', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
    }).then(function(response) {
        return response.json().then(function(data) {
            if (!response.ok || !data || !data.ok) {
                throw new Error((data && data.message) || 'Regenerate video submit failed');
            }
            if (button) button.textContent = 'Submitted';
            window.AICF.replaceSegmentCardFromResponse(segmentIndex, data, card, '.primary-mini');
            if (data.web_video_task_id) window.AICF.watchWebVideoTask(segmentIndex, data.web_video_task_id);
        });
    }).catch(function(error) {
        console.error('triggerSegmentVideoRegen failed', error);
        alert(error && error.message ? error.message : 'Regenerate video submit failed');
        if (button) {
            button.disabled = false;
            button.textContent = oldText || 'Regenerate video';
        }
    });
    return false;
};
window.AICF.triggerSegmentVideoRegenViaGradio = function(segmentIndex, payloadOverride, button) {
    if (window.AICF.archiveReadOnly) return false;
    var box = window.AICF.findGradioInput('segment_video_regen_payload');
    if (!box) { console.warn('triggerSegmentVideoRegen: input not found'); return false; }
    if (window.AICF.syncSegmentDirectorEditors) window.AICF.syncSegmentDirectorEditors();
    var textarea = window.AICF.segmentEditorFromButton
        ? window.AICF.segmentEditorFromButton(button, segmentIndex)
        : document.querySelector('.segment-director-editor[data-segment-index="' + String(segmentIndex || '') + '"]');
    var payload = payloadOverride || {segment_index: String(segmentIndex || ''), director_prompt: textarea ? (textarea.value || '') : ''};
    box.value = JSON.stringify(payload);
    box.dispatchEvent(new Event('input', {bubbles: true}));
    box.dispatchEvent(new Event('change', {bubbles: true}));
    var btn = window.AICF.findGradioBtn('segment_video_regen_btn');
    if (!btn) { console.warn('triggerSegmentVideoRegen: button not found'); return false; }
    setTimeout(function() { btn.click(); }, 80);
    return true;
};
window.AICF.toggleSegmentVideoOk = async function(segmentIndex, currentValue, button) {
    if (button && button.disabled) return false;
    var card = button && button.closest ? button.closest('.group-card') : null;
    var feedback = card ? card.querySelector('.segment-action-feedback') : null;
    var oldText = button ? button.textContent : '';
    var payload = {
        segment_index: Number(segmentIndex || 0),
        video_ok: !Boolean(currentValue)
    };
    if (button) {
        button.disabled = true;
        button.setAttribute('aria-busy', 'true');
        button.textContent = payload.video_ok ? '标记中...' : '取消中...';
    }
    if (feedback) {
        feedback.textContent = '';
        feedback.classList.remove('is-error');
    }
    try {
        var response = await _requestJson('/api/segment/video_ok', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });
        window.AICF.replaceSegmentCardFromResponse(segmentIndex, response, card, '.action-utility');
        if(response.learning) {
            var updatedCard = document.querySelector('.group-card[data-segment-index="'+segmentIndex+'"]') || window.AICF.segmentCardFromButton(button);
            var status = updatedCard && updatedCard.querySelector('.segment-action-feedback');
            if(status) {
                status.textContent = response.learning.message;
                status.classList.toggle('is-error', response.learning.state==='error');
            }
        }
    } catch (error) {
        var message = error && error.message ? error.message : '标记视频OK失败，请重试';
        if (feedback) {
            feedback.textContent = message;
            feedback.classList.add('is-error');
        }
        if (button) {
            button.disabled = false;
            button.removeAttribute('aria-busy');
            button.textContent = oldText || (payload.video_ok ? '标记视频OK' : '取消保护');
        }
    }
    return false;
};
window.AICF.segmentCardFromButton = function(button) {
    return button && button.closest ? button.closest('.group-card') : null;
};
window.AICF.segmentDisplayFromButton = function(button) {
    var card = window.AICF.segmentCardFromButton(button);
    if (!card) return { partId: '', title: '' };
    var partElement = card.querySelector('.segment-id');
    var titleElement = card.querySelector('.segment-title');
    return {
        partId: partElement ? String(partElement.textContent || '').trim() : '',
        title: titleElement ? String(titleElement.textContent || '').trim() : ''
    };
};
window.AICF.updatePromptModalSegmentContext = function(overlay, button) {
    if (!overlay) return;
    var context = window.AICF.segmentDisplayFromButton(button);
    var partElement = overlay.querySelector('.aicf-prompt-modal-part');
    var titleElement = overlay.querySelector('.aicf-prompt-modal-segment-title');
    if (partElement) partElement.textContent = context.partId || '当前分段';
    if (titleElement) {
        titleElement.textContent = context.title || '未命名分段';
        titleElement.title = context.title || '未命名分段';
    }
};
window.AICF.segmentEditorFromButton = function(button, segmentIndex) {
    var card = window.AICF.segmentCardFromButton(button);
    var selector = '.segment-director-editor[data-segment-index]';
    if (card) {
        var localTextarea = card.querySelector(selector);
        if (localTextarea && (!segmentIndex || String(localTextarea.dataset.segmentIndex || '') === String(segmentIndex))) {
            return localTextarea;
        }
        // A retained button can belong to a card that Gradio has already
        // recycled.  Never return that card's editor for a different index.
        if (!segmentIndex) return localTextarea;
    }
    if (!segmentIndex) return null;
    return document.querySelector('.segment-director-editor[data-segment-index="' + String(segmentIndex || '') + '"]');
};
window.AICF.promptSourceTextForEditor = function(textarea) {
    var root = textarea && textarea.closest ? textarea.closest('.prompt-block') : null;
    var source = root ? root.querySelector('.prompt-modal-source') : null;
    return source ? (source.textContent || '') : '';
};
window.AICF.syncSegmentDirectorEditors = function() {
    document.querySelectorAll('.segment-director-editor[data-segment-index]').forEach(function(textarea) {
        var segmentIndex = String(textarea.dataset.segmentIndex || '');
        var sourceText = window.AICF.promptSourceTextForEditor(textarea);
        if (!segmentIndex) return;
        // Segment indexes are reused by every episode.  Include the runtime
        // revision so switching an episode always replaces a reused DOM
        // textarea, even when two prompts happen to have the same shape.
        var runtimeRevision = String(window.AICF.promptRuntimeRevision || 0);
        var syncKey = runtimeRevision + ':' + segmentIndex + ':' + String(sourceText.length) + ':' + sourceText.slice(0, 24) + ':' + sourceText.slice(-24);
        if (textarea.dataset.aicfSyncKey === syncKey) return;
        // The groups panel is paged and Gradio may reuse the same textarea
        // element for a different segment.  An empty source is meaningful:
        // explicitly clear the reused editor instead of leaving the prompt
        // from the previous page visible.
        textarea.value = sourceText;
        textarea.defaultValue = sourceText;
        textarea.dataset.aicfSyncKey = syncKey;
    });
};
window.AICF.syncSegmentSourceExcerpts = function() {
    document.querySelectorAll('.segment-source-excerpt[data-segment-index]').forEach(function(textarea) {
        var root = textarea.closest ? textarea.closest('.segment-source-block') : null;
        var source = root ? root.querySelector('.segment-source-excerpt-source') : null;
        var sourceText = source ? (source.textContent || '') : '';
        var segmentIndex = String(textarea.dataset.segmentIndex || '');
        if (!segmentIndex) return;
        // Gradio can reuse a textarea DOM node when cards or pages change.
        // Read-only form values are preserved by the browser during that
        // morph, so restore the excerpt from a non-form source of truth.
        var runtimeRevision = String(window.AICF.promptRuntimeRevision || 0);
        var syncKey = runtimeRevision + ':' + segmentIndex + ':' + sourceText;
        if (textarea.dataset.aicfSourceSyncKey === syncKey && textarea.value === sourceText) return;
        textarea.value = sourceText;
        textarea.defaultValue = sourceText;
        textarea.dataset.aicfSourceSyncKey = syncKey;
    });
};
window.AICF.scheduleSegmentDirectorSync = function() {
    [0, 40, 120].forEach(function(delay) {
        setTimeout(function() {
            if (window.AICF && window.AICF.syncSegmentDirectorEditors) {
                window.AICF.syncSegmentDirectorEditors();
            }
            if (window.AICF && window.AICF.syncSegmentSourceExcerpts) {
                window.AICF.syncSegmentSourceExcerpts();
            }
        }, delay);
    });
};
window.AICF.observeSegmentDirectorEditors = function() {
    if (window.__AICF_SEGMENT_DIRECTOR_SYNC_BOUND__ || !window.MutationObserver || !document.body) return;
    window.__AICF_SEGMENT_DIRECTOR_SYNC_BOUND__ = true;
    var timer = null;
    var observer = new MutationObserver(function(mutations) {
        var shouldSync = false;
        for (var i = 0; i < mutations.length; i += 1) {
            var target = mutations[i].target;
            // Gradio can update the hidden prompt source by replacing a text
            // node.  Normalize that node to its parent so the editor is
            // synchronized immediately, not only after opening the modal.
            var elementTarget = target && target.nodeType === 3 ? target.parentElement : target;
            if (!elementTarget) continue;
            if (
                (elementTarget.matches && elementTarget.matches('.segment-director-editor, .prompt-modal-source, .segment-source-excerpt, .segment-source-excerpt-source, .group-card, .right-panel')) ||
                (elementTarget.querySelector && elementTarget.querySelector('.segment-director-editor, .prompt-modal-source, .segment-source-excerpt, .segment-source-excerpt-source'))
            ) {
                shouldSync = true;
                break;
            }
        }
        if (!shouldSync) return;
        clearTimeout(timer);
        timer = setTimeout(function() {
            if (window.AICF && window.AICF.syncSegmentDirectorEditors) {
                window.AICF.syncSegmentDirectorEditors();
            }
            if (window.AICF && window.AICF.syncSegmentSourceExcerpts) {
                window.AICF.syncSegmentSourceExcerpts();
            }
        }, 30);
    });
    observer.observe(document.body, {
        childList: true,
        subtree: true,
        characterData: true,
        attributes: true,
        attributeFilter: ['data-segment-index', 'data-render-key']
    });
    window.AICF.scheduleSegmentDirectorSync();
};
window.AICF.promptTextFromButton = function(button) {
    if (window.AICF.syncSegmentDirectorEditors) window.AICF.syncSegmentDirectorEditors();
    var textarea = window.AICF.segmentEditorFromButton(button);
    if (textarea) return textarea.value || '';
    var root = button && button.closest ? button.closest('.prompt-block') : null;
    var pre = root ? root.querySelector('.prompt-modal-source, .prompt-full') : null;
    return pre ? pre.textContent : '';
};
window.AICF.segmentFromResponse = function(segmentIndex, response) {
    var jobs = response && response.video_jobs && typeof response.video_jobs === 'object'
        ? response.video_jobs
        : response && typeof response === 'object' ? response : {};
    var segments = Array.isArray(jobs.segments) ? jobs.segments : [];
    return segments.find(function(segment) {
        return segment && Number(segment.segment_index || 0) === Number(segmentIndex || 0);
    }) || null;
};
window.AICF.formatPromptVersion = function(info, fallbackLabel) {
    info = info && typeof info === 'object' ? info : {};
    var version = Number(info.version || 0);
    var providerId = String(info.provider || fallbackLabel || '来源未记录');
    var provider = ({deepseek:'DeepSeek',chatgpt:'ChatGPT',doubao:'豆包',api:'DeepSeek API'})[providerId] || providerId;
    var rawTime = info.updated_at;
    var date = rawTime ? new Date(typeof rawTime === 'number' ? rawTime * 1000 : rawTime) : null;
    var time = date && !Number.isNaN(date.getTime()) ? date.toLocaleString('zh-CN', {hour12:false}) : '时间未记录';
    return (version ? '第 ' + version + ' 版' : '版本未记录') + ' · ' + provider + ' · ' + time;
};
window.AICF.promptTextMatches = function(left, right) {
    var normalise = function(value) { return String(value || '').trim().replace(/\r\n?/g, '\n'); };
    return !!normalise(right) && normalise(left) === normalise(right);
};
window.AICF.renderH3PromptVersion = function(root, data) {
    if (!root) return;
    data = data && typeof data === 'object' ? data : {};
    var node = root.querySelector('.aicf-prompt-version-meta');
    if (!node) return;
    var current = data.current || {};
    var revision = data.revision || {};
    var comparison = revision.prompt ? (data.matches ? '与最新修正版一致' : '与最新修正版不一致') : '暂无修正版可比对';
    node.textContent = 'H3 当前：' + window.AICF.formatPromptVersion(current, 'H3') + ' · ' + comparison;
    node.classList.toggle('is-match', !!data.matches);
    node.classList.toggle('is-different', !!revision.prompt && !data.matches);
    node.hidden = false;
};
window.AICF.updateSegmentCardState = function(segmentIndex, response) {
    var segment = window.AICF.segmentFromResponse(segmentIndex, response);
    var card = document.querySelector('.group-card[data-segment-index="' + String(segmentIndex || '') + '"]');
    if (!segment || !card) return card;
    var status = String(segment.status || '').toLowerCase();
    if (window.AICF._h3VideoRegenPending && window.AICF._h3VideoRegenPending[String(segmentIndex)]) status = 'submitting';
    var statusMap = {
        submitting: ['提交中', 'status-running'],
        workflow_ready: ['未生成', 'status-pending'],
        needs_regenerate: ['需要重新生成', 'status-stale'],
        stale: ['需要重新生成', 'status-stale'],
        running: ['生成中', 'status-running'],
        queued: ['排队中', 'status-pending'],
        pending: ['等待生成', 'status-pending'],
        failed: ['生成失败', 'status-failed'],
        error: ['生成失败', 'status-failed'],
        success: ['视频已生成', 'status-success']
    };
    var badge = card.querySelector('[data-segment-status]');
    if (badge && statusMap[status]) {
        badge.textContent = statusMap[status][0];
        badge.classList.remove('status-pending', 'status-stale', 'status-running', 'status-failed', 'status-success');
        badge.classList.add(statusMap[status][1]);
    }
    var backend = card.querySelector('[data-segment-backend] b');
    var task = card.querySelector('[data-segment-task] b');
    if (backend) backend.textContent = String(segment.backend || '未提交');
    if (task) task.textContent = String(segment.task_id || segment.prompt_id || '无');
    var config = segment.generation_config && typeof segment.generation_config === 'object' ? segment.generation_config : {};
    var job = segment.job && typeof segment.job === 'object' ? segment.job : {};
    var currentPrompt = String(config.prompt_override || job.final_prompt || job.prompt || '');
    var revision = config.latest_prompt_revision && typeof config.latest_prompt_revision === 'object' ? config.latest_prompt_revision : {};
    var matches = window.AICF.promptTextMatches(currentPrompt, revision.prompt);
    var saveBadge = card.querySelector('[data-prompt-save-status]');
    if (saveBadge) {
        var promptSaved = segment.prompt_saved !== false;
        saveBadge.classList.remove('status-success', 'status-pending');
        saveBadge.classList.add(promptSaved ? 'status-success' : 'status-pending');
        saveBadge.textContent = promptSaved ? '提示词已保存' : '提示词未保存';
        saveBadge.title = promptSaved ? '提示词已保存' : '提示词有未保存修改';
        if (revision.prompt) saveBadge.title += '；' + (matches ? '与最新修正版一致' : '与最新修正版不一致');
    }
    var versionData = {current:config.prompt_version_info || {}, revision:revision, matches:matches};
    var versionSource = card.querySelector('.h3-prompt-version-source');
    if (versionSource) versionSource.dataset.versionInfo = JSON.stringify(versionData);
    var modal = document.getElementById('aicf-prompt-modal');
    if (modal && modal.classList.contains('open') && String(modal.dataset.segmentIndex || '') === String(segmentIndex || '')) {
        window.AICF.renderH3PromptVersion(modal, versionData);
    }
    card.dataset.segmentStatus = status;
    return card;
};
window.AICF.replaceSegmentCardFromResponse = function(segmentIndex, response, currentCard, focusSelector) {
    var card = currentCard || document.querySelector('.group-card[data-segment-index="' + String(segmentIndex || '') + '"]');
    var html = String(response && response.segment_panel_html || '').trim();
    if (!card || !html) return window.AICF.updateSegmentCardState(segmentIndex, response);
    var host = document.createElement('template');
    host.innerHTML = html;
    var replacement = host.content.querySelector('.group-card[data-segment-index="' + String(segmentIndex || '') + '"]');
    if (!replacement) return window.AICF.updateSegmentCardState(segmentIndex, response);
    card.replaceWith(replacement);
    var focusTarget = focusSelector ? replacement.querySelector(focusSelector) : null;
    if (focusTarget && !focusTarget.disabled) {
        try { focusTarget.focus({preventScroll: true}); } catch (focusError) { focusTarget.focus(); }
    }
    if (window.AICF.scheduleSegmentDirectorSync) window.AICF.scheduleSegmentDirectorSync();
    return replacement;
};
window.AICF.workflowPromptDataFromButton = function(button) {
    var root = button && button.closest ? button.closest('.prompt-block') : null;
    var workflowSource = root ? root.querySelector('.workflow-prompt-source') : null;
    var prefixSource = root ? root.querySelector('.workflow-prompt-prefix-source') : null;
    var directorPrompt = window.AICF.promptTextFromButton(button);
    return {
        directorPrompt: directorPrompt || '',
        // The card's hidden source is the canonical director prompt. Keep the
        // compiled node-5 text as metadata for saving and reference previews.
        workflowPrompt: directorPrompt || '',
        compiledWorkflowPrompt: workflowSource ? (workflowSource.textContent || '') : (directorPrompt || ''),
        prefix: prefixSource ? (prefixSource.textContent || '') : ''
    };
};
window.AICF.segmentIndexFromButton = function(button) {
    var textarea = window.AICF.segmentEditorFromButton(button);
    if (textarea) return textarea.dataset.segmentIndex || '';
    var card = window.AICF.segmentCardFromButton(button);
    return card && card.dataset ? String(card.dataset.segmentIndex || '') : '';
};
window.AICF.triggerSegmentPromptSaveViaGradio = function(payload) {
    var box = window.AICF.findGradioInput('segment_prompt_save_payload');
    if (!box) { console.warn('saveSegmentPrompt: input not found'); return false; }
    box.value = JSON.stringify(payload || {});
    box.dispatchEvent(new Event('input', {bubbles: true}));
    box.dispatchEvent(new Event('change', {bubbles: true}));
    var btn = window.AICF.findGradioBtn('segment_prompt_save_btn');
    if (!btn) { console.warn('saveSegmentPrompt: button not found'); return false; }
    setTimeout(function() { btn.click(); }, 80);
    return true;
};
window.AICF.saveSegmentPrompt = async function(segmentIndex, button, overrideText, materialSelection) {
    if (window.AICF.archiveReadOnly) return;
    var textarea = window.AICF.segmentEditorFromButton(button, segmentIndex);
    if (textarea && textarea.dataset.segmentIndex) {
        segmentIndex = textarea.dataset.segmentIndex;
    }
    var text = typeof overrideText === 'string' ? overrideText : '';
    if (!text) {
        text = textarea ? (textarea.value || '') : '';
    }
    var payload = {segment_index: Number(segmentIndex || 0), director_prompt: text};
    if (materialSelection && Array.isArray(materialSelection.reference_roles)) {
        payload.reference_roles = materialSelection.reference_roles.slice(0, 4);
    }
    if (materialSelection && materialSelection.reference_image_ids && typeof materialSelection.reference_image_ids === 'object') {
        payload.reference_image_ids = Object.assign({}, materialSelection.reference_image_ids);
    }
    if (materialSelection && typeof materialSelection.scene_id === 'string') {
        payload.scene_id = materialSelection.scene_id;
    }
    if (materialSelection && typeof materialSelection.full_prompt === 'string') {
        payload.full_prompt = materialSelection.full_prompt;
    }
    if (materialSelection && materialSelection.build_workflow === false) {
        payload.build_workflow = false;
    }
    var hasFullPrompt = typeof payload.full_prompt === 'string' && payload.full_prompt.trim();
    if (!payload.segment_index || (!payload.director_prompt.trim() && !hasFullPrompt)) {
        throw new Error('分段序号和完整提示词不能为空');
    }
    var old = button ? button.textContent : '';
    if (button) {
        button.textContent = 'Saving...';
        button.disabled = true;
    }
    try {
        // The modal used to write a hidden Gradio input and immediately report
        // success. That loses the real backend result and can close the modal
        // before a failed save is visible. Persist through the API instead so
        // the save state always reflects the actual node-5 update.
        var response = await fetch('/api/segment/save_prompt', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });
        var result = await response.json().catch(function() { return {}; });
        if (!response.ok || !result.ok) {
            throw new Error(result.message || '保存分段提示词失败');
        }
        window.AICF.updateSegmentCardState(payload.segment_index, result);
        if (window.AICF.segmentPromptDrafts) delete window.AICF.segmentPromptDrafts[payload.segment_index];
        if (button) button.textContent = 'Saved';
        return result;
    } catch (error) {
        if (button) {
            button.disabled = false;
            button.textContent = old || 'Save prompt';
        }
        throw error;
    } finally {
        if (button) {
            setTimeout(function() {
                button.disabled = false;
                button.textContent = old || 'Save prompt';
            }, 900);
        }
    }
};
window.AICF.collectSegmentPromptEdits = function() {
    var drafts = window.AICF.segmentPromptDrafts || {};
    return Object.keys(drafts).map(function(index) {
        return {segment_index: Number(index), director_prompt: drafts[index] || ''};
    }).filter(function(edit) { return edit.segment_index > 0 && String(edit.director_prompt).trim(); });
};
window.AICF.segmentPromptDrafts = window.AICF.segmentPromptDrafts || {};
if (!window.AICF.segmentPromptDraftListenerInstalled) {
    document.addEventListener('input', function(event) {
        var textarea = event.target;
        if (!textarea || !textarea.matches || !textarea.matches('.segment-director-editor[data-segment-index]')) return;
        var index = Number(textarea.dataset.segmentIndex || 0);
        if (index > 0) window.AICF.segmentPromptDrafts[index] = textarea.value || '';
    });
    window.AICF.segmentPromptDraftListenerInstalled = true;
}
window.AICF.copyPromptFromButton = async function(button) {
    var text = window.AICF.promptTextFromButton(button);
    if (!text) return;
    try {
        await navigator.clipboard.writeText(text);
        var old = button.textContent;
        button.textContent = 'Copied';
        setTimeout(function() { button.textContent = old || '澶嶅埗 Prompt'; }, 1200);
    } catch (e) {
        var area = document.createElement('textarea');
        area.value = text;
        document.body.appendChild(area);
        area.select();
        document.execCommand('copy');
        document.body.removeChild(area);
    }
};
window.AICF.copySegmentSourceExcerpt = async function(button) {
    var root = button && button.closest ? button.closest('.prompt-block') : null;
    var area = root ? root.querySelector('.segment-source-excerpt') : null;
    var text = area ? String(area.value || '').trim() : '';
    if (!text) return;
    try {
        await navigator.clipboard.writeText(text);
        var old = button.textContent;
        button.textContent = 'Copied';
        setTimeout(function() { button.textContent = old || '复制原文'; }, 1200);
    } catch (e) {
        var tmp = document.createElement('textarea');
        tmp.value = text;
        document.body.appendChild(tmp);
        tmp.select();
        document.execCommand('copy');
        document.body.removeChild(tmp);
    }
};
window.AICF.materialDataFromButton = function(button) {
    var root = button && button.closest ? button.closest('.prompt-block') : null;
    // H3 opens from the card header, outside the prompt block.
    if (!root && button && button.closest) root = button.closest('.group-card');
    var source = root ? root.querySelector('.segment-material-source') : null;
    if (!source || !source.dataset.materials) return {characters: [], backgrounds: [], reference_roles: [], reference_image_ids: {}, scene_id: ''};
    try {
        var data = JSON.parse(source.dataset.materials);
        return data && typeof data === 'object' ? data : {characters: [], backgrounds: [], reference_roles: [], reference_image_ids: {}, scene_id: ''};
    } catch (e) {
        console.warn('materialDataFromButton: invalid material payload', e);
        return {characters: [], backgrounds: [], reference_roles: [], reference_image_ids: {}, scene_id: ''};
    }
};
window.AICF.assetFileUrl = function(path) {
    if (!path) return '';
    var base = '/gradio_api/file=' + String(path).replace(/\\/g, '/');
    return base + (base.indexOf('?') >= 0 ? '&' : '?') + 'v=' + Date.now();
};
window.AICF.setPromptModalFeedback = function(message, isError) {
    var ov = document.getElementById('aicf-prompt-modal');
    if (!ov) return;
    Array.prototype.forEach.call(ov.querySelectorAll('.aicf-material-feedback, .aicf-prompt-modal-feedback'), function(feedback) {
        feedback.textContent = message || '';
        feedback.classList.toggle('is-error', !!isError);
    });
};
window.AICF.promptModalMaterialSelection = function(ov) {
    ov = ov || document.getElementById('aicf-prompt-modal');
    var roles = Array.isArray(ov && ov._referenceRoles) ? ov._referenceRoles : [];
    var imageIds = ov && ov._referenceImageIds && typeof ov._referenceImageIds === 'object'
        ? ov._referenceImageIds
        : {};
    var normalizedRoles = roles.map(function(role) { return String(role || ''); }).filter(Boolean).slice(0, 4);
    var normalizedImageIds = {};
    normalizedRoles.forEach(function(role) {
        var imageId = String(imageIds[role] || '');
        if (imageId) normalizedImageIds[role] = imageId;
    });
    return {
        reference_roles: normalizedRoles,
        reference_image_ids: normalizedImageIds,
        scene_id: String((ov && ov._sceneId) || '')
    };
};
window.AICF.promptModalMaterialsChanged = function(ov) {
    if (!ov || !ov._initialMaterialSelection) return false;
    return JSON.stringify(window.AICF.promptModalMaterialSelection(ov)) !== JSON.stringify(ov._initialMaterialSelection);
};
window.AICF.promptModalDirectorText = function(ov) {
    ov = ov || document.getElementById('aicf-prompt-modal');
    var body = ov ? ov.querySelector('.aicf-prompt-modal-body') : null;
    var fullText = body ? (body.value || '') : '';
    var prefix = String((ov && ov._promptPrefix) || '').replace(/\s+$/, '');
    if (prefix && fullText.indexOf(prefix) === 0) {
        return fullText.slice(prefix.length).replace(/^\s+/, '');
    }
    return window.AICF.directorTextFromFullPrompt(fullText, ov && ov._directorPrompt);
};
window.AICF.directorTextFromFullPrompt = function(fullText, fallback) {
    var text = String(fullText || '').trim();
    if (!text) return String(fallback || '');
    var explicitMarker = '【导演分镜】';
    var explicitIndex = text.indexOf(explicitMarker);
    if (explicitIndex >= 0) {
        return text.slice(explicitIndex + explicitMarker.length).replace(/^\s+/, '');
    }
    var sceneMarkerPattern = /【(?:镜头内容|镜头脚本|场景内容|分镜内容|场景设定)】/g;
    var sceneMatch = sceneMarkerPattern.exec(text);
    if (sceneMatch) return text.slice(sceneMatch.index).trim();

    // The generated prefix ends with the identity-isolation paragraph.  A
    // pasted complete node-5 prompt normally starts the director body after
    // its next blank line, even if the user did not add a director heading.
    var isolationMarker = '【多角色身份隔离】';
    var isolationIndex = text.indexOf(isolationMarker);
    if (isolationIndex >= 0) {
        var afterIsolation = text.slice(isolationIndex + isolationMarker.length);
        var bodyStart = afterIsolation.search(/\n\s*\n/);
        if (bodyStart >= 0) {
            var isolatedBody = afterIsolation.slice(bodyStart).trim();
            if (isolatedBody) return isolatedBody;
        }
    }
    return text;
};
window.AICF.setPromptModalRefreshState = function(loading) {
    var ov = document.getElementById('aicf-prompt-modal');
    if (!ov) return;
    ov._promptRefreshPending = !!loading;
    ov.classList.toggle('is-refreshing', !!loading);
    var body = ov.querySelector('.aicf-prompt-modal-body');
    if (body) body.setAttribute('aria-busy', loading ? 'true' : 'false');
    var saveBtn = ov.querySelector('.aicf-prompt-modal-save');
    if (saveBtn) saveBtn.disabled = !!window.AICF.archiveReadOnly || !!loading;
};
window.AICF.refreshPromptModalEditor = async function() {
    var ov = document.getElementById('aicf-prompt-modal');
    if (!ov) return;
    var body = ov.querySelector('.aicf-prompt-modal-body');
    var directorPrompt = window.AICF.promptModalDirectorText(ov);
    window.AICF.setPromptModalRefreshState(true);
    window.AICF.setPromptModalFeedback('正在根据参考图更新完整提示词…', false);
    try {
        var response = await fetch('/api/segment/preview_prompt', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                segment_index: Number(ov.dataset.segmentIndex || 0),
                director_prompt: directorPrompt,
                reference_roles: Array.isArray(ov._referenceRoles) ? ov._referenceRoles.slice(0, 4) : [],
                reference_image_ids: ov._referenceImageIds && typeof ov._referenceImageIds === 'object'
                    ? Object.assign({}, ov._referenceImageIds)
                    : {}
            })
        });
        var data = await response.json().catch(function() { return {}; });
        if (!response.ok || !data.ok) {
            throw new Error(data.message || '更新完整提示词失败');
        }
        ov._directorPrompt = String(data.director_prompt || directorPrompt || '');
        ov._promptPrefix = String(data.prefix || '');
        ov._workflowPrompt = String(data.prompt || '');
        // Reference selection refreshes the compiled node-5 prompt only.  It
        // must not replace the director prompt being edited in the modal.
        window.AICF.setPromptModalFeedback('已更新参考图绑定，编辑内容保持不变', false);
    } finally {
        window.AICF.setPromptModalRefreshState(false);
    }
};
window.AICF.updatePromptModalMaterialState = function() {
    var ov = document.getElementById('aicf-prompt-modal');
    if (!ov) return;
    var roles = Array.isArray(ov._referenceRoles) ? ov._referenceRoles : [];
    var imageIds = ov._referenceImageIds && typeof ov._referenceImageIds === 'object' ? ov._referenceImageIds : {};
    var sceneId = String(ov._sceneId || '');
    ov.querySelectorAll('.aicf-material-option[data-kind="character-image"]').forEach(function(option) {
        var roleId = option.dataset.roleId || '';
        var imageId = option.dataset.imageId || '';
        var roleSelected = roles.indexOf(roleId) >= 0;
        var selected = roleSelected && String(imageIds[roleId] || '') === imageId;
        var referenceSlot = selected ? roles.indexOf(roleId) + 1 : 0;
        option.classList.toggle('is-selected', selected);
        option.classList.toggle('is-muted', !selected);
        option.setAttribute('aria-pressed', selected ? 'true' : 'false');
        option.dataset.referenceSlot = referenceSlot ? String(referenceSlot) : '';
        var state = option.querySelector('.aicf-material-option-state');
        if (state) {
            state.textContent = option.disabled
                ? '不可用'
                : (selected ? '参考图 #' + referenceSlot : (option.dataset.isPrimary === 'true' ? '默认主参考' : '未选择'));
        }
    });
    ov.querySelectorAll('.aicf-material-identity').forEach(function(group) {
        var roleIndex = roles.indexOf(group.dataset.roleId || '');
        var selected = roleIndex >= 0;
        group.classList.toggle('is-role-selected', selected);
        var state = group.querySelector('.aicf-material-identity-state');
        if (state) state.textContent = selected ? '已选 · #' + (roleIndex + 1) : '未选角色';
    });
    ov.querySelectorAll('.aicf-material-option[data-kind="background"]').forEach(function(option) {
        var selected = sceneId === (option.dataset.materialId || '');
        option.classList.toggle('is-selected', selected);
        option.classList.toggle('is-muted', !selected);
        option.setAttribute('aria-pressed', selected ? 'true' : 'false');
        var state = option.querySelector('.aicf-material-option-state');
        if (state) state.textContent = option.disabled ? '不可用' : (selected ? '当前背景' : '未选择');
    });
    var count = ov.querySelector('.aicf-character-count');
    if (count) count.textContent = '已选 ' + roles.length + '/4';
};
window.AICF.createPromptModalMaterialOption = function(kind, item) {
    var ov = document.getElementById('aicf-prompt-modal');
    var option = document.createElement('button');
    option.type = 'button';
    option.className = 'aicf-material-option is-muted';
    option.dataset.kind = kind;
    option.dataset.materialId = String(item.id || '');
    option.disabled = !item.available || !!window.AICF.archiveReadOnly;
    option.setAttribute('aria-pressed', 'false');
    option.setAttribute('aria-label', String(item.display_name || item.asset_id || item.id || '绱犳潗'));

    var media = document.createElement('span');
    media.className = 'aicf-material-option-media';
    if (item.image_path) {
        var image = document.createElement('img');
        image.src = window.AICF.assetFileUrl(item.image_path);
        image.alt = String(item.display_name || item.asset_id || '绱犳潗鍥剧墖');
        image.loading = 'lazy';
        media.appendChild(image);
    } else {
        var missing = document.createElement('span');
        missing.className = 'aicf-material-missing';
        missing.textContent = 'Image missing';
        media.appendChild(missing);
    }

    var text = document.createElement('span');
    text.className = 'aicf-material-option-text';
    var name = document.createElement('strong');
    name.textContent = String(item.display_name || item.asset_id || item.id || 'Untitled asset');
    var meta = document.createElement('small');
    meta.textContent = String(item.asset_id || item.id || '');
    text.appendChild(name);
    text.appendChild(meta);

    var state = document.createElement('span');
    state.className = 'aicf-material-option-state';
    state.textContent = item.available ? 'Not selected' : 'Unavailable';
    option.appendChild(media);
    option.appendChild(text);
    option.appendChild(state);
    option.addEventListener('click', function() {
        if (!ov) return;
        if (kind === 'character') {
            var roleId = option.dataset.materialId || '';
            var index = ov._referenceRoles.indexOf(roleId);
            if (index >= 0) {
                ov._referenceRoles.splice(index, 1);
            } else if (ov._referenceRoles.length >= 4) {
                window.AICF.setPromptModalFeedback('Select up to 4 character reference images.', true);
                return;
            } else {
                ov._referenceRoles.push(roleId);
            }
        } else {
            ov._sceneId = option.dataset.materialId || '';
        }
        window.AICF.setPromptModalFeedback('', false);
        window.AICF.updatePromptModalMaterialState();
    });
    return option;
};
window.AICF.createPromptModalCharacterGroup = function(item) {
    var ov = document.getElementById('aicf-prompt-modal');
    var roleId = String(item.id || '');
    var group = document.createElement('section');
    group.className = 'aicf-material-identity';
    group.dataset.roleId = roleId;

    var head = document.createElement('div');
    head.className = 'aicf-material-identity-head';
    var heading = document.createElement('div');
    var name = document.createElement('strong');
    name.textContent = String(item.display_name || item.asset_id || roleId || '未命名人物');
    var meta = document.createElement('small');
    var count = Array.isArray(item.images) ? item.images.length : 0;
    meta.textContent = String(item.asset_id || roleId) + ' · ' + count + ' 张参考图';
    heading.appendChild(name);
    heading.appendChild(meta);
    var roleState = document.createElement('span');
    roleState.className = 'aicf-material-identity-state';
    roleState.textContent = '未选角色';
    head.appendChild(heading);
    head.appendChild(roleState);
    group.appendChild(head);

    var images = document.createElement('div');
    images.className = 'aicf-character-images';
    var options = Array.isArray(item.images) ? item.images : [];
    if (!options.length) {
        var empty = document.createElement('div');
        empty.className = 'aicf-material-empty';
        empty.textContent = '该人物没有可用参考图';
        images.appendChild(empty);
    }
    options.forEach(function(imageItem) {
        var option = document.createElement('button');
        option.type = 'button';
        option.className = 'aicf-material-option is-muted';
        option.dataset.kind = 'character-image';
        option.dataset.roleId = roleId;
        option.dataset.imageId = String(imageItem.id || '');
        option.dataset.isPrimary = imageItem.is_primary ? 'true' : 'false';
        option.disabled = !imageItem.available || !item.available || !!window.AICF.archiveReadOnly;
        option.setAttribute('aria-pressed', 'false');
        option.setAttribute(
            'aria-label',
            String(item.display_name || roleId) + '，' + String(imageItem.display_name || imageItem.id || '参考图')
        );

        var media = document.createElement('span');
        media.className = 'aicf-material-option-media';
        if (imageItem.image_path) {
            var image = document.createElement('img');
            image.src = window.AICF.assetFileUrl(imageItem.image_path);
            image.alt = String(imageItem.display_name || imageItem.id || '人物参考图');
            image.loading = 'lazy';
            media.appendChild(image);
        } else {
            var missing = document.createElement('span');
            missing.className = 'aicf-material-missing';
            missing.textContent = '图片缺失';
            media.appendChild(missing);
        }

        var text = document.createElement('span');
        text.className = 'aicf-material-option-text';
        var optionName = document.createElement('strong');
        optionName.textContent = String(imageItem.display_name || imageItem.id || '未命名参考图');
        var optionMeta = document.createElement('small');
        optionMeta.textContent = String(imageItem.id || '');
        text.appendChild(optionName);
        text.appendChild(optionMeta);

        var state = document.createElement('span');
        state.className = 'aicf-material-option-state';
        state.textContent = option.disabled ? '不可用' : (imageItem.is_primary ? '默认主参考' : '未选择');
        option.appendChild(media);
        option.appendChild(text);
        option.appendChild(state);
        option.addEventListener('click', function() {
            if (!ov || ov._promptRefreshPending) return;
            var previousRoles = ov._referenceRoles.slice();
            var previousImageIds = Object.assign({}, ov._referenceImageIds);
            var imageId = option.dataset.imageId || '';
            var index = ov._referenceRoles.indexOf(roleId);
            var isExactSelection = index >= 0 && String(ov._referenceImageIds[roleId] || '') === imageId;
            if (isExactSelection) {
                ov._referenceRoles.splice(index, 1);
                delete ov._referenceImageIds[roleId];
            } else {
                if (index < 0 && ov._referenceRoles.length >= 4) {
                    window.AICF.setPromptModalFeedback('人物参考最多选择 4 个角色。', true);
                    return;
                }
                if (index < 0) ov._referenceRoles.push(roleId);
                ov._referenceImageIds[roleId] = imageId;
            }
            window.AICF.setPromptModalFeedback('', false);
            window.AICF.updatePromptModalMaterialState();
            window.AICF.refreshPromptModalEditor().catch(function(error) {
                ov._referenceRoles = previousRoles;
                ov._referenceImageIds = previousImageIds;
                window.AICF.updatePromptModalMaterialState();
                window.AICF.setPromptModalFeedback(
                    error && error.message ? error.message : '更新完整提示词失败',
                    true
                );
            });
        });
        images.appendChild(option);
    });
    group.appendChild(images);
    return group;
};
window.AICF.renderPromptModalMaterials = function(data) {
    var ov = document.getElementById('aicf-prompt-modal');
    if (!ov) return;
    var characters = Array.isArray(data.characters) ? data.characters : [];
    var backgrounds = Array.isArray(data.backgrounds) ? data.backgrounds : [];
    var availableRoleIds = characters
        .filter(function(item) {
            return item.available && Array.isArray(item.images) && item.images.some(function(image) { return image.available; });
        })
        .map(function(item) { return String(item.id || ''); });
    var availableSceneIds = backgrounds.filter(function(item) { return item.available; }).map(function(item) { return String(item.id || ''); });
    ov._materialData = data;
    ov._referenceRoles = (Array.isArray(data.reference_roles) ? data.reference_roles : [])
        .map(function(role) { return String(role || ''); })
        .filter(function(role, index, all) { return role && availableRoleIds.indexOf(role) >= 0 && all.indexOf(role) === index; })
        .slice(0, 4);
    var incomingImageIds = data.reference_image_ids && typeof data.reference_image_ids === 'object'
        ? data.reference_image_ids
        : {};
    ov._referenceImageIds = {};
    ov._referenceRoles.forEach(function(roleId) {
        var character = characters.find(function(item) { return String(item.id || '') === roleId; });
        var options = character && Array.isArray(character.images)
            ? character.images.filter(function(image) { return image.available; })
            : [];
        var requestedId = String(incomingImageIds[roleId] || '');
        var selected = options.find(function(image) { return String(image.id || '') === requestedId; });
        if (!selected) selected = options.find(function(image) { return !!image.is_primary; }) || options[0];
        if (selected && selected.id) ov._referenceImageIds[roleId] = String(selected.id);
    });
    ov._sceneId = availableSceneIds.indexOf(String(data.scene_id || '')) >= 0 ? String(data.scene_id || '') : '';
    if (!ov._sceneId && availableSceneIds.length) {
        ov._sceneId = availableSceneIds[0];
        window.AICF.setPromptModalFeedback('Original background is unavailable; selected the first available background.', false);
    } else {
        window.AICF.setPromptModalFeedback('', false);
    }
    ov._initialMaterialSelection = window.AICF.promptModalMaterialSelection(ov);

    var characterGrid = ov.querySelector('.aicf-character-materials');
    var backgroundGrid = ov.querySelector('.aicf-background-materials');
    if (characterGrid) {
        characterGrid.replaceChildren();
        if (!characters.length) {
            var emptyCharacters = document.createElement('div');
            emptyCharacters.className = 'aicf-material-empty';
            emptyCharacters.textContent = 'No character assets selected';
            characterGrid.appendChild(emptyCharacters);
        } else {
            characters.forEach(function(item) {
                characterGrid.appendChild(window.AICF.createPromptModalCharacterGroup(item));
            });
        }
    }
    if (backgroundGrid) {
        backgroundGrid.replaceChildren();
        if (!backgrounds.length) {
            var emptyBackgrounds = document.createElement('div');
            emptyBackgrounds.className = 'aicf-material-empty';
            emptyBackgrounds.textContent = 'No background assets selected';
            backgroundGrid.appendChild(emptyBackgrounds);
        } else {
            backgrounds.forEach(function(item) { backgroundGrid.appendChild(window.AICF.createPromptModalMaterialOption('background', item)); });
        }
    }
    window.AICF.updatePromptModalMaterialState();
};
window.AICF.handlePromptModalKeydown = function(event) {
    var ov = document.getElementById('aicf-prompt-modal');
    if (!ov || !ov.classList.contains('open')) return;
    if (event.key === 'Escape') {
        event.preventDefault();
        window.AICF.closePromptModal();
        return;
    }
    if (event.key !== 'Tab') return;
    var focusable = Array.from(ov.querySelectorAll('button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'))
        .filter(function(element) { return element.offsetParent !== null; });
    if (!focusable.length) return;
    var first = focusable[0];
    var last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
    }
};
window.AICF.resetPromptModalOperationState = function(ov) {
    // Busy flags belong to the previous editor session. Keep actual save
    // promises intact so an accepted backend write still serializes correctly.
    ov._editorBusy = false;
    ov._operationPending = false;
    ov._promptTaskBusy = false;
    delete ov._promptTaskOwner;
    ov._sceneStateBusy = false;
    ov._h3SceneConfigBusy = false;
    ov._sceneStateTask = null;
    ov._sceneStatePending = Promise.resolve();
    delete ov._sceneCreatePending;
    delete ov._ensureScene;
};
window.AICF.openPromptModalFromButton = function(button) {
    // Finish the previous session before cloning another editor. A template's
    // inert content can survive Gradio pagination and is never data authority.
    var previousModal = document.getElementById('aicf-prompt-modal');
    if (previousModal && previousModal._h3AutosaveTimer) {
        window.clearTimeout(previousModal._h3AutosaveTimer);
        previousModal._h3AutosaveTimer = null;
        window.AICF.h3AutosavePending = window.AICF.saveH3GenerationConfig(previousModal.dataset.segmentIndex, null, previousModal);
    }
    var opening = Number(window.AICF.h3OpeningRequest || 0) + 1;
    window.AICF.h3OpeningRequest = opening;
    var pendingSave = window.AICF.h3AutosavePending || (previousModal && previousModal._h3SavePending);
    if (pendingSave) {
        pendingSave.then(function() {
            if (window.AICF.h3AutosavePending === pendingSave) window.AICF.h3AutosavePending = null;
            if (window.AICF.h3OpeningRequest === opening && button && button.isConnected) window.AICF.openPromptModalFromButton(button);
        }).catch(function(error) {
            if (window.AICF.h3AutosavePending === pendingSave) window.AICF.h3AutosavePending = null;
            window.AICF.setPromptModalFeedback(error.message || '参考配置保存失败，请重试。', true);
        });
        return;
    }
    var promptData = window.AICF.workflowPromptDataFromButton(button);
    var segmentIndex = window.AICF.segmentIndexFromButton(button);
    var materialData = window.AICF.materialDataFromButton(button);
    var segmentCard = window.AICF.segmentCardFromButton(button);
    var modelId = segmentCard && segmentCard.dataset ? String(segmentCard.dataset.modelId || '') : '';
    var isH3 = modelId === 'minimax_h3_local_ref2va';
    if (isH3 && segmentCard) {
        // The H3 fullscreen button sits in the card header, outside the prompt
        // block. Resolve its editor value from the card's canonical hidden source.
        var h3PromptSource = segmentCard.querySelector('.h3-card-prompt-source');
        if (h3PromptSource) {
            promptData.directorPrompt = String(h3PromptSource.textContent || '');
            promptData.workflowPrompt = promptData.directorPrompt;
            promptData.compiledWorkflowPrompt = promptData.directorPrompt;
        }
    }
    var ov = document.getElementById('aicf-prompt-modal');
    if (!ov) {
        ov = document.createElement('div');
        ov.id = 'aicf-prompt-modal';
        ov.className = 'aicf-prompt-modal';
        ov.innerHTML = '<div class="aicf-prompt-modal-card" role="dialog" aria-modal="true" aria-labelledby="aicf-prompt-modal-title"><div class="aicf-prompt-modal-head"><div class="aicf-prompt-modal-context"><strong id="aicf-prompt-modal-title">导演分镜全屏编辑</strong><div class="aicf-prompt-modal-segment" aria-label="当前编辑分段"><span class="aicf-prompt-modal-part">当前分段</span><span class="aicf-prompt-modal-segment-title">未命名分段</span></div></div><div class="aicf-prompt-modal-actions"><button class="aicf-prompt-modal-save" onclick="window.AICF.savePromptModal()">保存</button><button class="aicf-prompt-modal-replan" hidden onclick="window.AICF.replanPromptModal()">保存并重新规划时长</button><button class="aicf-prompt-modal-force-save" hidden title="按页面当前图片顺序和提示词保存，并更新素材绑定" onclick="window.AICF.savePromptModal(true)">强制保存</button><button class="aicf-prompt-modal-close" aria-label="关闭" title="关闭" onclick="window.AICF.closePromptModal()">&#10005;</button></div></div><div class="aicf-prompt-modal-content"><aside class="aicf-prompt-material-panel"><div class="aicf-material-panel-title">本段参考素材</div><section class="aicf-material-section"><div class="aicf-material-section-head"><strong>人物参考图</strong><span class="aicf-character-count">已选 0/4</span></div><div class="aicf-material-grid aicf-character-materials"></div></section><section class="aicf-material-section"><div class="aicf-material-section-head"><strong>背景参考图</strong><span>单选</span></div><div class="aicf-material-grid aicf-background-materials"></div></section><div class="aicf-material-feedback" role="status" aria-live="polite"></div></aside><div class="aicf-prompt-editor-pane"><div class="aicf-prompt-source-context"><label for="aicf-prompt-source-excerpt">剧情上下文（source_excerpt）</label><small>补充理解本段所需的前因背景；镜头仍只表现当前 Beat。</small><textarea id="aicf-prompt-source-excerpt" spellcheck="false" aria-label="剧情上下文"></textarea></div><div class="aicf-prompt-editor-head"><label for="aicf-prompt-modal-editor">导演分镜提示词</label><small>仅在此处编辑本段提示词；可从项目已选人物中最多选择 4 张参考图，保存后会同步参考绑定。</small><div class="aicf-prompt-version-meta" hidden></div><div class="aicf-prompt-modal-feedback" role="status" aria-live="polite"></div></div><textarea id="aicf-prompt-modal-editor" class="aicf-prompt-modal-body" spellcheck="false"></textarea></div></div></div>';
        var modalContent = ov.querySelector('.aicf-prompt-modal-content');
        var ltxPanel = ov.querySelector('.aicf-prompt-material-panel');
        var editorPane = ov.querySelector('.aicf-prompt-editor-pane');
        if (ltxPanel) ltxPanel.classList.add('aicf-ltx-material-panel');
        if (modalContent && editorPane) {
            var h3Panel = document.createElement('aside');
            h3Panel.className = 'aicf-prompt-material-panel aicf-h3-config-panel';
            h3Panel.hidden = true;
            modalContent.insertBefore(h3Panel, editorPane);
        }
        ov.addEventListener('click', function(ev) { if (ev.target === ov) window.AICF.closePromptModal(); });
        ov.addEventListener('keydown', window.AICF.handlePromptModalKeydown);
        var modalHost = document.querySelector('.gradio-container .contain') || document.querySelector('.gradio-container') || document.body;
        modalHost.appendChild(ov);
    }
    // The fullscreen editor is a singleton.  A delayed autosave created for
    // the previously opened segment must never read the newly cloned panel
    // and save those references under the old segment index.
    if (ov._h3AutosaveTimer) {
        window.clearTimeout(ov._h3AutosaveTimer);
        ov._h3AutosaveTimer = null;
    }
    ov._h3Session = Number(ov._h3Session || 0) + 1;
    window.AICF.resetPromptModalOperationState(ov);
    // Keep tracking the same segment, without blocking an unrelated editor.
    if(ov._sourceReplanPending && String(ov.dataset.segmentIndex)===String(segmentIndex) &&
        ov._sourceReplanPending.runtime===String(window.AICF.promptRuntimeRevision || 0)) {
        ov._sourceReplanPending.session=String(ov._h3Session);
    } else delete ov._sourceReplanPending;
    delete ov._syncSourceReplan;
    ov._previousFocus = button || document.activeElement;
    ov._sourceButton = button || null;
    ov._directorPrompt = String(promptData.directorPrompt || '');
    ov._promptPrefix = String(promptData.prefix || '');
    ov._workflowPrompt = String(promptData.compiledWorkflowPrompt || promptData.workflowPrompt || ov._directorPrompt || '');
    ov._promptRefreshPending = false;
    ov.dataset.segmentIndex = segmentIndex || '';
    ov.dataset.modelId = modelId;
    ov.dataset.h3Session = String(ov._h3Session);
    window.AICF.setPromptModalFeedback('', false);
    if(window.AICF.renderH3DurationPlanning)window.AICF.renderH3DurationPlanning(ov,null);
    delete ov.dataset.promptRevisionPrompt;
    delete ov.dataset.promptProvenance;
    window.AICF.updatePromptModalSegmentContext(ov, button);
    var modalTitle = ov.querySelector('#aicf-prompt-modal-title');
    var promptLabel = ov.querySelector('.aicf-prompt-editor-head > label');
    var promptHelp = ov.querySelector('.aicf-prompt-editor-head > small');
    var ltxMaterialPanel = ov.querySelector('.aicf-ltx-material-panel');
    var h3ConfigPanel = ov.querySelector('.aicf-h3-config-panel');
    if (ltxMaterialPanel) ltxMaterialPanel.hidden = isH3;
    if (h3ConfigPanel) {
        h3ConfigPanel.hidden = !isH3;
        h3ConfigPanel.replaceChildren();
        if (isH3 && segmentCard) {
            var h3Template = segmentCard.querySelector('.h3-fullscreen-config-template');
            if (h3Template && h3Template.content) h3ConfigPanel.appendChild(h3Template.content.cloneNode(true));
            try {
                window.AICF.hydrateH3ModalConfig(h3ConfigPanel, segmentCard, segmentIndex);
            } catch (error) {
                ov.classList.remove('open');
                window.alert(error.message);
                return;
            }
        }
    }
    if (modalTitle) modalTitle.textContent = isH3 ? 'H3 视频提示词全屏编辑' : '导演分镜全屏编辑';
    if (promptLabel) promptLabel.textContent = isH3 ? 'H3 视频提示词' : '导演分镜提示词';
    var promptCount = ov.querySelector('.aicf-prompt-char-count');
    if (!promptCount && promptLabel) {
        promptCount = document.createElement('span');
        promptCount.className = 'aicf-prompt-char-count';
        promptCount.setAttribute('role', 'status');
        promptCount.setAttribute('aria-live', 'polite');
        promptLabel.insertAdjacentElement('afterend', promptCount);
    }
    if (promptCount) { promptCount.hidden = !isH3; promptCount.textContent = '计算中…'; }
    if (promptHelp) {
        promptHelp.textContent = isH3
            ? '此处编辑镜头、动作和台词正文；主体、参考及音色信息在“自动参考信息”中只读查看，由素材和入画名单生成。'
            : '此处与页面提示词框编辑同一内容。可从项目已选人物中最多选择 4 张参考图；保存后会同步参考绑定。';
    }
    var promptVersionSource = segmentCard && segmentCard.querySelector('.h3-prompt-version-source');
    var promptVersionData = {};
    try { promptVersionData = JSON.parse(promptVersionSource && promptVersionSource.dataset.versionInfo || '{}'); } catch (error) {}
    var promptVersionMeta = ov.querySelector('.aicf-prompt-version-meta');
    if (promptVersionMeta) promptVersionMeta.hidden = !isH3;
    if (isH3) window.AICF.renderH3PromptVersion(ov, promptVersionData);
    var body = ov.querySelector('.aicf-prompt-modal-body');
    if (window.AICF.installH3PromptEditor) window.AICF.installH3PromptEditor(ov, isH3);
    if (body) {
        body.value = ov._directorPrompt;
        body.readOnly = !!window.AICF.archiveReadOnly;
        body.setAttribute('aria-readonly', window.AICF.archiveReadOnly ? 'true' : 'false');
        body.setAttribute('aria-busy', 'false');
    }
    var excerpt = ov.querySelector('#aicf-prompt-source-excerpt');
    var excerptSource = segmentCard && segmentCard.querySelector('.segment-source-excerpt-source');
    if (excerpt) {
        excerpt.value = excerptSource ? String(excerptSource.textContent || '') : '';
        excerpt.dataset.savedValue = excerpt.value;
        excerpt.readOnly = !!window.AICF.archiveReadOnly;
    }
    var saveBtn = ov.querySelector('.aicf-prompt-modal-save');
    if (saveBtn) saveBtn.disabled = !!window.AICF.archiveReadOnly;
    var replanBtn = ov.querySelector('.aicf-prompt-modal-replan');
    if (replanBtn) { replanBtn.hidden = !isH3; replanBtn.disabled = !!window.AICF.archiveReadOnly; }
    var forceSaveBtn = ov.querySelector('.aicf-prompt-modal-force-save');
    if (forceSaveBtn) { forceSaveBtn.hidden = !isH3; forceSaveBtn.disabled = !!window.AICF.archiveReadOnly; }
    if (isH3) {
        if (window.AICF.renderH3ProjectImages) window.AICF.renderH3ProjectImages(h3ConfigPanel, materialData);
        window.AICF.scheduleH3PromptCount(ov);
        if (body) body.oninput = function() { window.AICF.scheduleH3PromptCount(ov); };
        if (window.AICF.refreshH3AssetCount) window.AICF.refreshH3AssetCount(h3ConfigPanel);
        if (h3ConfigPanel) {
            h3ConfigPanel.querySelectorAll('.h3-asset-input').forEach(function(input) {
                input.addEventListener('change', function() {
                    if (window.AICF.scheduleH3ConfigAutosave) window.AICF.scheduleH3ConfigAutosave(h3ConfigPanel);
                });
            });
        }
    } else {
        window.AICF.renderPromptModalMaterials(materialData);
    }
    ov.classList.add('open');
    if (window.AICF.mountPromptRevision) window.AICF.mountPromptRevision(ov);
    window.AICF.mountVideoChannels(ov.dataset.segmentIndex);
    setTimeout(function() { if (body && !window.AICF.archiveReadOnly) body.focus(); }, 30);
};
window.AICF.savePromptModal = async function(forceSave, contentOnly) {
    if (window.AICF.archiveReadOnly) return;
    var ov = document.getElementById('aicf-prompt-modal');
    if (!ov) return;
    if (ov._promptTaskBusy || ov._editorBusy) {
        window.AICF.setPromptModalFeedback('正在保存或提交，请稍候。', false);
        return;
    }
    if (ov._sourceReplanPending) {
        var waitingSession = String(ov.dataset.h3Session || '');
        try { if (ov._syncSourceReplan) await ov._syncSourceReplan(); }
        catch (error) { window.AICF.setPromptModalFeedback(error.message || '同步时长规划失败', true); return; }
        if (String(ov.dataset.h3Session || '') !== waitingSession) return;
        if (ov._sourceReplanPending) {
            window.AICF.setPromptModalFeedback('时长规划正在执行，请等待结果同步后再保存。', false);
            return;
        }
    }
    if (contentOnly === true && (!ov._staleOptimizationDraft || ov._staleOptimizationDraft !== ov._editorDraft)) {
        window.AICF.setPromptModalFeedback('优化草稿已变化，请重新保存并检查当前状态。', true);
        return;
    }
    if (ov._shotEditor && ov._shotEditor.dirty && ov._shotEditor.stale &&
        ov._shotEditor.documents.some(function(doc) { return doc.new || doc.removed || doc.structure_changed; })) {
        window.AICF.setPromptModalFeedback('当前文字与未保存的跨段 Shot 结构不一致，请先保存结构修改或恢复 Shot 草稿；当前文字已保留。', true);
        return;
    }
    var forcedEditorDraft = forceSave === true || contentOnly === true ? ov._editorDraft : null;
    if (ov._editorDraft && !forcedEditorDraft) {
        try { await window.AICF.saveH3EditorDraft(ov, true); }
        catch (error) { window.AICF.setPromptModalFeedback(error.message || '保存完整编辑稿失败', true); }
        return;
    }
    if (!forcedEditorDraft && ov._shotEditor && ov._shotEditor.stale && typeof ov._shotEditor.read === 'function') {
        try {
            if (!await ov._shotEditor.read(false)) throw new Error('当前文字无法读取为分镜，尚未保存，请检查镜头格式。');
        } catch (error) { window.AICF.setPromptModalFeedback(error.message, true); return; }
    }
    if (ov._shotEditor && ov._shotEditor.dirty && !ov._shotEditor.stale) {
        window.AICF.setPromptModalFeedback('正在保存 Shot 草稿…', false);
        try {
            var shotSaved = await ov._shotEditor.save();
            if (!shotSaved) throw new Error('Shot 草稿未提交，请重新打开编辑器后重试；请先复制备份当前文字。');
        }
        catch (error) { window.AICF.setPromptModalFeedback(error.message || 'Shot 保存失败', true); }
        return;
    }
    if (ov._promptRefreshPending) {
        window.AICF.setPromptModalFeedback('参考图提示词正在更新，请稍候。', false);
        return;
    }
    var segmentIndex = ov.dataset.segmentIndex || '';
    var modalSession = String((ov.dataset && ov.dataset.h3Session) || '');
    var body = ov.querySelector('.aicf-prompt-modal-body');
    var directorText = body ? String(body.value || '').trim() : '';
    window.AICF.setPromptModalFeedback('正在保存当前提示词…', false);
    try { await window.AICF.savePromptSourceExcerpt(false); }
    catch (error) { window.AICF.setPromptModalFeedback(error.message || '保存剧情上下文失败', true); return; }
    if (!directorText) {
        window.AICF.setPromptModalFeedback('导演分镜提示词不能为空。', true);
        return;
    }
    try {
        if (ov._savePromptDuration) await ov._savePromptDuration();
    } catch (error) {
        window.AICF.setPromptModalFeedback(error.message || '保存时长失败', true);
        return;
    }
    if (String(ov.dataset.modelId || '') === 'minimax_h3_local_ref2va') {
        try {
            var h3Response = await window.AICF.saveH3GenerationConfig(segmentIndex, ov.querySelector(forceSave === true ? '.aicf-prompt-modal-force-save' : '.aicf-prompt-modal-save'), ov, forceSave === true);
            if (!h3Response) throw new Error('当前分段保存未完成，编辑稿仍保留，请重试。');
            if (
                String((ov.dataset && ov.dataset.segmentIndex) || '') !== String(segmentIndex)
                || String((ov.dataset && ov.dataset.h3Session) || '') !== modalSession
            ) return;
            if (forcedEditorDraft && ov._editorDraft === forcedEditorDraft) {
                window.AICF.clearH3EditorDraft(ov);
                if (h3Response.editor_snapshot && window.AICF.syncH3EditorBaseline) window.AICF.syncH3EditorBaseline(ov, h3Response.editor_snapshot);
                else if (window.AICF.refreshH3EditorBaseline) await window.AICF.refreshH3EditorBaseline(ov);
                if (window.AICF.recordH3OptimizationContentSave) await window.AICF.recordH3OptimizationContentSave(ov, forcedEditorDraft.draft.optimization_attempt_id);
            }
            var savedDirectorText = String(h3Response && h3Response.canonical_prompt || directorText);
            var h3CardTextarea = window.AICF.segmentEditorFromButton(ov._sourceButton, segmentIndex);
            if (h3CardTextarea) {
                h3CardTextarea.value = savedDirectorText;
            }
            var h3Card = window.AICF.segmentCardFromButton(ov._sourceButton);
            var h3PromptSource = h3Card && h3Card.querySelector('.h3-card-prompt-source');
            if (h3PromptSource) h3PromptSource.textContent = savedDirectorText;
            window.AICF.setPromptModalFeedback(contentOnly === true ? '当前文字与素材已保存；未确认过期的优化状态。' : forceSave === true ? '已按页面图片顺序和提示词强制保存，素材绑定已更新' : 'H3 提示词与参考配置已保存', false);
            setTimeout(function() {
                if (
                    String((ov.dataset && ov.dataset.segmentIndex) || '') === String(segmentIndex)
                    && String((ov.dataset && ov.dataset.h3Session) || '') === modalSession
                ) window.AICF.closePromptModal();
            }, 220);
        } catch (error) {
            window.AICF.setPromptModalFeedback(error && error.message ? error.message : '保存 H3 配置失败', true);
        }
        return;
    }
    var materialSelection = window.AICF.promptModalMaterialSelection(ov);
    var materialsChanged = window.AICF.promptModalMaterialsChanged(ov);
    if (materialsChanged && !materialSelection.scene_id) {
        window.AICF.setPromptModalFeedback('Select one background reference image.', true);
        return;
    }
    try {
        var savePayload = {};
        if (materialsChanged) {
            savePayload.reference_roles = materialSelection.reference_roles;
            savePayload.reference_image_ids = materialSelection.reference_image_ids;
            savePayload.scene_id = materialSelection.scene_id;
        }
        await window.AICF.saveSegmentPrompt(segmentIndex, ov.querySelector('.aicf-prompt-modal-save'), directorText, savePayload);
        if (
            String((ov.dataset && ov.dataset.segmentIndex) || '') !== String(segmentIndex)
            || String((ov.dataset && ov.dataset.h3Session) || '') !== modalSession
        ) return;
        var cardTextarea = window.AICF.segmentEditorFromButton(ov._sourceButton, segmentIndex);
        if (cardTextarea) {
            cardTextarea.value = directorText;
        }
        window.AICF.setPromptModalFeedback(materialsChanged ? '已保存提示词和本段参考素材' : '已保存提示词', false);
        setTimeout(function() {
            if (
                String((ov.dataset && ov.dataset.segmentIndex) || '') === String(segmentIndex)
                && String((ov.dataset && ov.dataset.h3Session) || '') === modalSession
            ) window.AICF.closePromptModal();
        }, 220);
    } catch (error) {
        window.AICF.setPromptModalFeedback(error && error.message ? error.message : '保存失败', true);
    }
};
window.AICF.readPageLLMProviders = function(ov) {
    if (ov._h3CompositeRun && ov._h3CompositeRun.providers) return ov._h3CompositeRun.providers.slice();
    var providers = Array.from(ov.querySelectorAll('.review-provider-options input:checked'))
        .filter(function(input) { return !input.disabled; }).map(function(input) { return input.value; });
    if (!providers.length) throw new Error('请先勾选本页面使用的 LLM');
    return providers;
};
window.AICF.readPromptReplanOptions = function(ov) {
    var durationField = ov && ov.querySelector('.review-duration');
    if (!durationField || durationField.disabled) throw new Error('本段时长设置尚未加载，请稍后重试');
    var duration = Number(durationField.value);
    if (!Number.isFinite(duration) || duration <= 0) throw new Error('本段参考时长请输入正数，视频提交范围为 4–15 秒');
    var providers = Array.from(ov.querySelectorAll('.review-provider-options input:checked'))
        .filter(function(input) { return !input.disabled; })
        .map(function(input) { return input.value; });
    var apiInput = ov.querySelector('.review-timing-api');
    var timingApi = !!(apiInput && apiInput.checked && !apiInput.disabled);
    if (!providers.length && !timingApi) throw new Error('请勾选网页 LLM 或 DeepSeek API（仅规划时长）');
    return {duration_sec:duration, providers:providers, timing_api:timingApi};
};
window.AICF.savePromptBeforeTask = async function(ov) {
    const A=window.AICF,session=String(ov.dataset.h3Session),index=String(ov.dataset.segmentIndex),runtime=String(A.promptRuntimeRevision||0);
    const valid=()=>ov.classList.contains('open')&&String(ov.dataset.h3Session)===session&&String(ov.dataset.segmentIndex)===index&&String(A.promptRuntimeRevision||0)===runtime;
    if(A.archiveReadOnly)throw new Error('当前分段只读，不能保存并提交。');
    if(ov._h3AutosaveTimer){clearTimeout(ov._h3AutosaveTimer);ov._h3AutosaveTimer=null;}
    if(A.h3AutosavePending)await A.h3AutosavePending;
    if(ov._h3SavePending)await ov._h3SavePending;
    if(!valid())throw new Error('编辑分段已切换，已取消提交。');
    if(ov._editorDraft){await A.saveH3EditorDraft(ov,true);if(ov._editorDraft)throw new Error('完整编辑稿尚未保存，本次未提交');}
    if(!valid())throw new Error('编辑分段已切换，已取消提交。');
    if(String(ov.dataset.modelId)!=='minimax_h3_local_ref2va') {
        await ov._savePromptDuration();await A.savePromptSourceExcerpt(false);
        await A.saveSegmentPrompt(index,null,ov.querySelector('.aicf-prompt-modal-body').value,{});
        return {segment_index:Number(index)};
    }
    const shot=ov._shotEditor,editor=ov.querySelector('.aicf-prompt-modal-body');
    if(!shot||shot.reading||shot.saving)throw new Error('分镜尚在读取或保存，请稍候再提交。');
    if(shot.stale||editor.value!==shot.lastPrompt) {
        if(shot.dirty&&shot.documents.some(d=>d.new||d.removed||d.structure_changed))throw new Error('文字与未保存的跨段 Shot 结构不一致，请先保存结构修改，本次未提交。');
        if(!await shot.read(false))throw new Error('当前文字无法读取为分镜，请修正后再提交。');
    }
    const manifest=A.readH3Manifest?A.readH3Manifest(ov):JSON.parse(ov.querySelector('.h3-asset-list')?.dataset.manifest||'[]');
    const uploads=[...ov.querySelectorAll('.h3-asset-input')].some(input=>input.files?.length);
    if(uploads||(A.h3ManifestSignature||JSON.stringify)(manifest)!==shot.manifestBase) {
        if(shot.dirty)throw new Error('素材与分镜同时存在未保存修改，请先统一素材和当前分镜后保存，本次未提交。');
        await A.saveH3GenerationConfig(index,null,ov);
        if(!valid()||!await shot.read(false))throw new Error('素材已保存，请重新读取分镜后提交。');
        shot.dirty=true;
    }
    const source=shot.documents.find(d=>d.uid===shot.sourceUid);
    if(!source||source.removed||!source.shots.length)throw new Error('当前分段已空或已删除，请先保存整理，再打开目标分段提交。');
    const excerpt=ov.querySelector('#aicf-prompt-source-excerpt');
    if(excerpt&&excerpt.value!==String(excerpt.dataset.savedValue||''))shot.dirty=true;
    if(shot.dirty&&!await shot.saveForTask())throw new Error('当前分镜未保存成功，本次未提交。');
    if(!valid())throw new Error('内容已保存，但编辑分段已切换，本次未提交。');
    return {segment_index:Number(index),segment_uid:shot.sourceUid,prompt:editor.value};
};
window.AICF.runSavedPromptAction = async function(ov,submit) {
    if(ov._promptTaskBusy)throw new Error('正在保存或提交，请勿重复点击。');
    ov._promptTaskBusy=true;
    const session=String(ov.dataset.h3Session),owner={};ov._promptTaskOwner=owner;
    const controls=[...ov.querySelectorAll('input,textarea,select,button')].map(el=>[el,el.disabled]);
    const editor=ov.querySelector('.aicf-prompt-modal-body'),readOnly=editor.readOnly;
    controls.forEach(([el])=>{el.disabled=true;});editor.readOnly=true;
    let saved=false;
    try{const target=await window.AICF.savePromptBeforeTask(ov);saved=true;return await submit(target);}
    catch(error){error.saved=saved;throw error;}
    finally{
        if(ov._promptTaskOwner===owner){ov._promptTaskBusy=false;delete ov._promptTaskOwner;}
        if(String(ov.dataset.h3Session)===session){editor.readOnly=readOnly;controls.forEach(([el,disabled])=>{if(el.isConnected)el.disabled=disabled;});ov._shotEditor?.refreshControls?.();}
    }
};
window.AICF.savePromptSourceExcerpt = async function(replan, submittedOptions) {
    var ov = document.getElementById('aicf-prompt-modal');
    var field = ov && ov.querySelector('#aicf-prompt-source-excerpt');
    if (!ov || !field || window.AICF.archiveReadOnly) return null;
    var value = String(field.value || '');
    if (value === String(field.dataset.savedValue || '') && !replan) return null;
    var index = Number(ov.dataset.segmentIndex || 0);
    var submittedSession=String(ov.dataset.h3Session || ''),submittedRuntime=String(window.AICF.promptRuntimeRevision || 0);
    var stillCurrent=function(){return String(ov.dataset.h3Session || '')===submittedSession &&
        Number(ov.dataset.segmentIndex || 0)===index && String(window.AICF.promptRuntimeRevision || 0)===submittedRuntime;};
    var replanOptions = {};
    var submittedEditorText = '';
    if (replan === true) {
        replanOptions = submittedOptions || window.AICF.readPromptReplanOptions(ov);
    }
    var payload = {segment_index:index, source_excerpt:value, regenerate_prompt:replan === true,
        editors:{story_editor:document.querySelector('#story_editor textarea')?.value ?? '', beats_editor:document.querySelector('#beats_editor textarea')?.value ?? ''}};
    if (replan === true) {
        payload.duration_sec = replanOptions.duration_sec;
        payload.providers = replanOptions.providers;
        payload.timing_api = replanOptions.timing_api === true;
        submittedEditorText = String(ov.querySelector('.aicf-prompt-modal-body')?.value || '');
        payload.editor_prompt = submittedEditorText;
        if (!payload.editor_prompt.trim()) throw new Error('编辑区提示词为空，无法重新规划时长');
        payload.asset_manifest = _h3Manifest(ov);
    }
    var sourceHeaders={'Content-Type':'application/json'};
    if(ov._editorRuntime){sourceHeaders['X-AICF-Runtime']=encodeURIComponent(ov._editorRuntime);sourceHeaders['X-AICF-Runtime-Encoding']='uri';}
    var response = await fetch('/api/segment/source-excerpt', {
        method: 'POST', headers: sourceHeaders,
        body: JSON.stringify(payload)
    });
    var result = await response.json().catch(function(){return {};});
    if(!stillCurrent())return result;
    if (!response.ok || !result.ok) throw new Error(result.message || '保存剧情上下文失败');
    var card = window.AICF.segmentCardFromButton(ov._sourceButton);
    if (!replan) {
        field.dataset.savedValue = value;
        var source = card && card.querySelector('.segment-source-excerpt-source');
        var view = card && card.querySelector('.segment-source-excerpt');
        if (source) source.textContent = value;
        if (view) view.value = value;
    }
    if (result.operation_id) {
        ov._sourceReplanPending = {id:result.operation_id, session:String(ov.dataset.h3Session || ''),
            runtime:String(window.AICF.promptRuntimeRevision || 0),
            prompt:submittedEditorText, duration:String(replanOptions.duration_sec),
            manifest:(window.AICF.h3ManifestSignature || JSON.stringify)(payload.asset_manifest)};
        window.AICF.setPromptModalFeedback(result.message, false); return result;
    }
    if (replan && result.canonical_prompt) {
        var prompt = ov.querySelector('.aicf-prompt-modal-body');
        if (prompt) { prompt.value = result.canonical_prompt; prompt.dispatchEvent(new Event('input',{bubbles:true})); }
        var h3SaveButton = ov.querySelector('.aicf-prompt-modal-save');
        if (h3SaveButton && window.AICF.saveH3GenerationConfig) {
            await window.AICF.saveH3GenerationConfig(index, h3SaveButton, ov);
            if(!stillCurrent())return result;
        }
        card = window.AICF.segmentCardFromButton(ov._sourceButton) || card;
        var cardPrompt = card && card.querySelector('.h3-prompt-override');
        if (cardPrompt) cardPrompt.value = result.canonical_prompt;
        var preview = card && card.querySelector('.h3-card-prompt-source, .prompt-modal-source');
        if (preview) preview.textContent = result.canonical_prompt;
        var summary = card && card.querySelector('.prompt-summary small');
        if (summary) summary.textContent = result.canonical_prompt.length + ' 字符 / ' + (result.canonical_prompt ? result.canonical_prompt.split('\n').length : 0) + ' 行';
        window.AICF.setPromptModalFeedback('提示词时长已重新规划，并同步对应 Beat。', false);
    }
    return result;
};
window.AICF.replanPromptModal = async function() {
    var ov = document.getElementById('aicf-prompt-modal');
    if (!ov || window.AICF.archiveReadOnly) return;
    var replanSession=String(ov.dataset.h3Session || ''),replanRuntime=String(window.AICF.promptRuntimeRevision || 0);
    var replanCurrent=function(){return String(ov.dataset.h3Session || '')===replanSession && String(window.AICF.promptRuntimeRevision || 0)===replanRuntime;};
    if(ov._promptTaskBusy){window.AICF.setPromptModalFeedback('正在保存或提交，请稍候。',true);return;}
    var button = ov.querySelector('.aicf-prompt-modal-replan');
    try {
        if(ov._sourceReplanPending && ov._syncSourceReplan)await ov._syncSourceReplan();
        if(ov._sourceReplanPending){window.AICF.setPromptModalFeedback('已有时长规划任务正在执行，请稍候。',true);return;}
        const options=window.AICF.readPromptReplanOptions(ov);
        if(button){button.disabled=true;button.textContent='正在保存并规划…';}
        await window.AICF.runSavedPromptAction(ov,async()=>{
            window.AICF.setPromptModalFeedback('当前编辑已保存，正在提交时长规划…',false);
            return await window.AICF.savePromptSourceExcerpt(true,options);
        });
    } catch (error) {
        if(replanCurrent())window.AICF.setPromptModalFeedback((error.saved?'当前编辑已保存，时长规划提交失败：':'保存或提交未完成：')+(error.message||'请重试'), true);
    } finally {
        if (button && replanCurrent()) { button.disabled=false; button.textContent='保存并重新规划时长'; }
    }
};
window.AICF.closePromptModal = function() {
    var ov = document.getElementById('aicf-prompt-modal');
    if (!ov) return;
    if (window.AICF.cancelH3Composite) window.AICF.cancelH3Composite(ov);
    if (ov._shotEditor && ov._shotEditor.stop) ov._shotEditor.stop();
    if (ov._webRevisionStop) ov._webRevisionStop();
    ov.querySelectorAll('audio').forEach(function(player) { player.pause(); });
    ov.classList.remove('open');
    if (ov._previousFocus && typeof ov._previousFocus.focus === 'function') {
        ov._previousFocus.focus();
    }
};
window.AICF.markSegmentPromptDirty = function(textarea) {
    if (window.AICF.archiveReadOnly) return;
    var card = textarea && textarea.closest ? textarea.closest('.group-card') : null;
    if (!card) return;
    var badge = card.querySelector('.group-actions .status-badge');
    if (!badge) return;
    badge.textContent = '鎻愮ず璇嶆湭淇濆瓨';
    badge.classList.remove('status-success');
    badge.classList.add('status-pending');
};
if (!window.__AICF_SEGMENT_EDITOR_DIRTY_BOUND__) {
    window.__AICF_SEGMENT_EDITOR_DIRTY_BOUND__ = true;
    document.addEventListener('input', function(evt) {
        var target = evt.target;
        if (target && target.classList && target.classList.contains('segment-director-editor')) {
            window.AICF.markSegmentPromptDirty(target);
        }
    }, true);
}
window.AICF.syncSegmentsPanelHeight = function() {
    var panel = document.querySelector('.output-column .right-panel');
    var beatsEditor = document.getElementById('beats_editor');
    if (!panel || !beatsEditor) return;
    if (window.innerWidth < 1100) {
        panel.style.removeProperty('--aicf-segments-panel-min-height');
        return;
    }
    var target = beatsEditor;
    var saveBtn = document.getElementById('save_story_beats_btn');
    var beatsRect = target.getBoundingClientRect();
    var saveRect = saveBtn ? saveBtn.getBoundingClientRect() : null;
    var panelRect = panel.getBoundingClientRect();
    var targetBottom = Math.max(beatsRect.bottom, saveRect ? saveRect.bottom : beatsRect.bottom);
    var height = Math.ceil(targetBottom - panelRect.top);
    if (!Number.isFinite(height) || height < 520) return;
    panel.style.setProperty('--aicf-segments-panel-min-height', height + 'px');
};
window.AICF.scheduleSegmentsPanelHeightSync = function() {
    if (window.__AICF_SEGMENTS_HEIGHT_SYNC__) cancelAnimationFrame(window.__AICF_SEGMENTS_HEIGHT_SYNC__);
    window.__AICF_SEGMENTS_HEIGHT_SYNC__ = requestAnimationFrame(function() {
        window.__AICF_SEGMENTS_HEIGHT_SYNC__ = null;
        if (window.AICF && window.AICF.syncSegmentsPanelHeight) window.AICF.syncSegmentsPanelHeight();
    });
};
window.AICF.toggleVideoOkSegmentVisibility = function(button) {
    if (!button || button.disabled) return false;
    var trigger = window.AICF.findGradioBtn ? window.AICF.findGradioBtn('segment_ok_filter_btn') : null;
    if (!trigger) {
        console.warn('toggleVideoOkSegmentVisibility: trigger not found');
        return false;
    }
    button.disabled = true;
    trigger.click();
    return false;
};
// ---- CodeMirror story/beats editor with inline quality markers ----
window.AICF.ensureCodeMirror = function(callback) {
    if (window.CodeMirror) { callback(true); return; }
    if (window.__AICF_CODEMIRROR_LOADING__) {
        window.__AICF_CODEMIRROR_QUEUE__ = window.__AICF_CODEMIRROR_QUEUE__ || [];
        window.__AICF_CODEMIRROR_QUEUE__.push(callback);
        return;
    }
    window.__AICF_CODEMIRROR_LOADING__ = true;
    window.__AICF_CODEMIRROR_QUEUE__ = [callback];
    var css = document.createElement('link');
    css.rel = 'stylesheet';
    css.href = '/static/codemirror/codemirror.min.css';
    document.head.appendChild(css);
    var script = document.createElement('script');
    script.src = '/static/codemirror/codemirror.min.js';
    script.onload = function() {
        var queue = window.__AICF_CODEMIRROR_QUEUE__ || [];
        window.__AICF_CODEMIRROR_LOADING__ = false;
        queue.forEach(function(fn) { try { fn(true); } catch (e) {} });
        window.__AICF_CODEMIRROR_QUEUE__ = [];
    };
    script.onerror = function() {
        console.warn('[AICF] local CodeMirror load failed, fallback to textarea');
        var queue = window.__AICF_CODEMIRROR_QUEUE__ || [];
        window.__AICF_CODEMIRROR_LOADING__ = false;
        queue.forEach(function(fn) { try { fn(false); } catch (e) {} });
        window.__AICF_CODEMIRROR_QUEUE__ = [];
    };
    document.head.appendChild(script);
};
window.AICF.renderCodeMirrorQuality = function(state) {
    if (!state || !state.cm) return;
    (state.marks || []).forEach(function(mark) { try { mark.clear(); } catch (e) {} });
    state.marks = [];
    try { state.cm.clearGutter('aicf-risk-gutter'); } catch (e) {}
    var value = state.cm.getValue() || '';
    var issues = window.AICF.currentQualityIssues(value);
    var note = state.note;
    if (note) note.style.display = issues.length ? 'block' : 'none';
    if (note) {
        var badge = note.querySelector('.quality-risk-badge');
        var detail = note.querySelector('.quality-risk-detail');
        if (badge) {
            badge.innerHTML = '<span class="quality-risk-icon">鈿?/span><span>' + issues.length + ' 鏉￠闄?/span><span class="quality-risk-chevron">灞曞紑</span>';
            badge.setAttribute('aria-expanded', note.classList.contains('is-expanded') ? 'true' : 'false');
        }
        if (detail) {
            detail.innerHTML = issues.map(function(issue, index) {
                return '<div class="quality-risk-note-item"><strong>椋庨櫓' + (index + 1) + '</strong><span class="quality-risk-phrase">' + window.AICF.escapeHtml(issue.phrase) + '</span><span class="quality-risk-reason">' + window.AICF.escapeHtml(issue.reason) + '</span></div>';
            }).join('');
        }
    }
    var lineIssues = {};
    issues.forEach(function(issue, issueIndex) {
        var phrase = issue.phrase || '';
        var start = 0;
        while (phrase && start < value.length) {
            var index = value.indexOf(phrase, start);
            if (index < 0) break;
            var pos = state.cm.posFromIndex(index);
            if (!lineIssues[pos.line]) lineIssues[pos.line] = [];
            lineIssues[pos.line].push({index: issueIndex + 1, phrase: phrase, reason: issue.reason || '瑙嗛鐢熸垚椋庨櫓'});
            start = index + Math.max(phrase.length, 1);
        }
    });
    Object.keys(lineIssues).forEach(function(lineKey) {
        var line = Number(lineKey);
        var lineNotes = lineIssues[line] || [];
        var marker = document.createElement('span');
        marker.className = 'cm-risk-arrow';
        marker.textContent = '>';
        marker.title = lineNotes.map(function(item) {
            return 'Risk ' + item.index + ': ' + item.phrase + ' - ' + item.reason;
        }).join('\n');
        try { state.cm.setGutterMarker(line, 'aicf-risk-gutter', marker); } catch (e) {}
    });
};
window.AICF.renderNativeRiskArrows = function(textarea, wrap, issues, value) {
    if (!textarea || !wrap) return;
    var layer = wrap.querySelector('.native-risk-arrow-layer');
    if (!layer) {
        layer = document.createElement('div');
        layer.className = 'native-risk-arrow-layer';
        layer.setAttribute('aria-hidden', 'true');
        wrap.appendChild(layer);
    }
    layer.innerHTML = '';
    layer.style.top = (textarea.offsetTop || 0) + 'px';
    layer.style.left = ((textarea.offsetLeft || 0) + 10) + 'px';
    layer.style.height = (textarea.clientHeight || textarea.offsetHeight || 0) + 'px';
    if (!issues || !issues.length) return;
    var text = String(value || textarea.value || '');
    var style = window.getComputedStyle(textarea);
    var lineHeight = parseFloat(style.lineHeight) || 20;
    var paddingTop = parseFloat(style.paddingTop) || 0;
    var borderTop = parseFloat(style.borderTopWidth) || 0;
    var lineMap = {};
    issues.forEach(function(issue, issueIndex) {
        var phrase = issue.phrase || '';
        var start = 0;
        while (phrase && start < text.length) {
            var index = text.indexOf(phrase, start);
            if (index < 0) break;
            var line = text.slice(0, index).split('\n').length - 1;
            if (!lineMap[line]) lineMap[line] = [];
            lineMap[line].push({index: issueIndex + 1, phrase: phrase, reason: issue.reason || '瑙嗛鐢熸垚椋庨櫓'});
            start = index + Math.max(phrase.length, 1);
        }
    });
    Object.keys(lineMap).forEach(function(lineKey) {
        var line = Number(lineKey);
        var notes = lineMap[line] || [];
        var arrow = document.createElement('div');
        arrow.className = 'native-risk-arrow';
        arrow.textContent = '>';
        arrow.style.top = Math.max(0, paddingTop + borderTop + line * lineHeight - textarea.scrollTop) + 'px';
        arrow.title = notes.map(function(item) {
            return 'Risk ' + item.index + ': ' + item.phrase + ' - ' + item.reason;
        }).join('\n');
        layer.appendChild(arrow);
    });
};
window.AICF.mountCodeMirrorEditor = function(id) {
    var editor = document.getElementById(id);
    var textarea = editor ? editor.querySelector('textarea') : null;
    if (!editor || !textarea || textarea._aicfCodeMirrorBound || !window.CodeMirror) return;
    textarea._aicfCodeMirrorBound = true;
    var wrap = textarea.parentElement || editor;
    wrap.classList.add('quality-editor-shell', 'aicf-cm-host');
    var header = document.createElement('div');
    header.className = 'quality-editor-header';
    var title = document.createElement('div');
    title.className = 'quality-editor-title';
    title.textContent = id === 'story_editor' ? 'Story' : 'Beats';
    var note = document.createElement('div');
    note.className = 'quality-risk-summary';
    note.setAttribute('aria-live', 'polite');
    note.innerHTML = '<button type="button" class="quality-risk-badge" aria-expanded="false"></button><div class="quality-risk-detail"></div>';
    header.appendChild(title);
    header.appendChild(note);
    wrap.insertBefore(header, textarea);
    var badge = note.querySelector('.quality-risk-badge');
    if (badge) {
        badge.addEventListener('click', function() {
            note.classList.toggle('is-expanded');
            badge.setAttribute('aria-expanded', note.classList.contains('is-expanded') ? 'true' : 'false');
        });
    }
    var cm = window.CodeMirror.fromTextArea(textarea, {
        lineNumbers: true,
        gutters: ['aicf-risk-gutter', 'CodeMirror-linenumbers'],
        lineWrapping: true,
        mode: 'text/plain',
        indentUnit: 2,
        tabSize: 2,
        viewportMargin: 80,
        extraKeys: {
            'Ctrl-S': function() { if (window.AICF.archiveReadOnly) return; var btn = window.AICF.findGradioBtn('save_story_beats_btn'); if (btn) btn.click(); },
            'Cmd-S': function() { if (window.AICF.archiveReadOnly) return; var btn = window.AICF.findGradioBtn('save_story_beats_btn'); if (btn) btn.click(); }
        }
    });
    cm.setOption('readOnly', window.AICF.archiveReadOnly ? 'nocursor' : false);
    cm.getWrapperElement().classList.add('aicf-codemirror', id === 'story_editor' ? 'aicf-codemirror-story' : 'aicf-codemirror-beats');
    var state = {cm: cm, textarea: textarea, note: note, marks: [], lastTextareaValue: textarea.value || '', syncing: false};
    textarea._aicfCmState = state;
    cm.on('change', function() {
        if (state.syncing) return;
        var value = cm.getValue();
        state.lastTextareaValue = value;
        textarea.value = value;
        textarea.dispatchEvent(new Event('input', {bubbles: true}));
        window.AICF.renderCodeMirrorQuality(state);
    });
    window.AICF.renderCodeMirrorQuality(state);
};
window.AICF.bindQualityHighlightPreviews = function() {
    ['story_editor', 'beats_editor'].forEach(function(id) {
        var editor = document.getElementById(id);
        var textarea = editor ? editor.querySelector('textarea') : null;
        if (!editor || !textarea || textarea._aicfNativeQualityBound) return;
        textarea._aicfNativeQualityBound = true;
        var wrap = textarea.parentElement || editor;
        wrap.classList.add('quality-editor-shell', 'quality-native-host');
        if (!wrap.querySelector('.quality-editor-header')) {
            var header = document.createElement('div');
            header.className = 'quality-editor-header';
            var title = document.createElement('div');
            title.className = 'quality-editor-title';
            title.textContent = id === 'story_editor' ? 'Story' : 'Beats';
            var note = document.createElement('div');
            note.className = 'quality-risk-summary';
            note.setAttribute('aria-live', 'polite');
            note.innerHTML = '<button type="button" class="quality-risk-badge" aria-expanded="false"></button><div class="quality-risk-detail"></div>';
            header.appendChild(title);
            header.appendChild(note);
            wrap.insertBefore(header, textarea);
            var badge = note.querySelector('.quality-risk-badge');
            if (badge) {
                badge.addEventListener('click', function() {
                    note.classList.toggle('is-expanded');
                    badge.setAttribute('aria-expanded', note.classList.contains('is-expanded') ? 'true' : 'false');
                });
            }
        }
        var render = function() {
            var raw = textarea.value || '';
            var clean = window.AICF.stripQualityAnnotations(raw);
            if (raw !== clean && !textarea._aicfCleaningQualityText) {
                textarea._aicfCleaningQualityText = true;
                textarea.value = clean;
                textarea.dispatchEvent(new Event('input', {bubbles: true}));
                textarea._aicfCleaningQualityText = false;
                raw = clean;
            }
            var issues = window.AICF.currentQualityIssues(raw);
            var note = wrap.querySelector('.quality-risk-summary');
            textarea.classList.toggle('native-quality-risk', issues.length > 0);
            window.AICF.renderNativeRiskArrows(textarea, wrap, issues, raw);
            if (note) {
                note.style.display = issues.length ? 'block' : 'none';
                var badge = note.querySelector('.quality-risk-badge');
                var detail = note.querySelector('.quality-risk-detail');
                if (badge) badge.innerHTML = '<span class="quality-risk-icon">鈿?/span><span>' + issues.length + ' 鏉￠闄?/span><span class="quality-risk-chevron">灞曞紑</span>';
                if (detail) {
                    detail.innerHTML = issues.map(function(issue, index) {
                        return '<div class="quality-risk-note-item"><strong>椋庨櫓' + (index + 1) + '</strong><span class="quality-risk-phrase">' + window.AICF.escapeHtml(issue.phrase) + '</span><span class="quality-risk-reason">' + window.AICF.escapeHtml(issue.reason) + '</span></div>';
                    }).join('');
                }
            }
        };
        textarea.addEventListener('input', render);
        textarea.addEventListener('change', render);
        textarea.addEventListener('scroll', render);
        render();
    });
};
// Legacy aliases
window.triggerKeyImgRegen = window.AICF.triggerKeyImgRegen;
window.triggerPartKeyImgRegen = window.AICF.triggerPartKeyImgRegen;
window._findGradioBtn = window.AICF.findGradioBtn;
window._findGradioInput = window.AICF.findGradioInput;
    console.log('[AICF] JS namespace ready');

    window.AICF.bindAssetLibraryFields = function() {
        var root = document.getElementById('asset_library_page');
        if (!root) return;
        root.querySelectorAll('.asset-text-field input, .asset-text-field textarea, .asset-path-box input, .asset-path-box textarea').forEach(function(field) {
            if (field._aicfAssetFieldBound) return;
            field._aicfAssetFieldBound = true;
            field.setAttribute('autocomplete', 'off');
            field.setAttribute('autocorrect', 'off');
            field.setAttribute('autocapitalize', 'off');
            field.setAttribute('spellcheck', 'false');
            field.addEventListener('focus', function() {
                var block = field.closest('.asset-text-field, .asset-path-box');
                if (!block) return;
                block.querySelectorAll('[title]').forEach(function(el) {
                    var title = el.getAttribute('title') || '';
                    if (!title) return;
                    el.dataset.aicfSuppressedTitle = title;
                    el.removeAttribute('title');
                });
            });
        });
    };

    // Attach deferred event listeners (elements may render after APP_JS)
    function _attachListeners() {
        if (window.AICF && window.AICF.theme) {
            window.AICF.theme.apply(window.AICF.theme.current());
            window.AICF.theme.mount();
        }
        if (window.AICF && window.AICF.bindDevDrawerInteractions) {
            window.AICF.bindDevDrawerInteractions();
        }
        if (window.AICF && window.AICF.bindTopActionButtons) {
            window.AICF.bindTopActionButtons();
        }
        if (window.AICF && window.AICF.bindQualityHighlightPreviews) {
            window.AICF.bindQualityHighlightPreviews();
        }
        if (window.AICF && window.AICF.schedulePageTabSync) {
            window.AICF.schedulePageTabSync();
        }
        if (window.AICF && window.AICF.observePageTabs) {
            window.AICF.observePageTabs();
        }
        if (window.AICF && window.AICF.observeSegmentDirectorEditors) {
            window.AICF.observeSegmentDirectorEditors();
        }
        if (window.AICF && window.AICF.scheduleSegmentDirectorSync) {
            window.AICF.scheduleSegmentDirectorSync();
        }
        if (window.AICF && window.AICF.scheduleSegmentsPanelHeightSync) {
            window.AICF.scheduleSegmentsPanelHeightSync();
        }
        if (window.AICF && window.AICF.bindAssetLibraryFields) {
            window.AICF.bindAssetLibraryFields();
        }
        if (window.AICF && window.AICF.bindTargetBeatCountInput) {
            window.AICF.bindTargetBeatCountInput();
        }
        var fbtn = document.getElementById('final_video_preview_btn');
        if (fbtn && !fbtn._previewBound) {
            fbtn._previewBound = true;
            fbtn.addEventListener('click', function() {
                var box = document.querySelector('#final_video_section textarea') || document.querySelector('#final_video_section input');
                if (box && box.value) window._previewVideo('/gradio_api/file=' + box.value.replace(/\\/g, '/'));
            });
        }
        var topicBox = document.querySelector('#topic_editor textarea, #topic_editor input');
        var storyBox = document.querySelector('#story_editor textarea');
        var beatsBox = document.querySelector('#beats_editor textarea');
        [topicBox, storyBox, beatsBox].forEach(function(box) {
            if (!box || box._saveShortcutBound) return;
            box._saveShortcutBound = true;
            box.addEventListener('keydown', function(ev) {
                if ((ev.ctrlKey || ev.metaKey) && String(ev.key || '').toLowerCase() === 's') {
                    ev.preventDefault();
                    var btn = window.AICF.findGradioBtn('save_story_beats_btn');
                    if (btn) btn.click();
                }
            });
        });
    }
    document.addEventListener('DOMContentLoaded', _attachListeners);
    setTimeout(_attachListeners, 500);
    setTimeout(_attachListeners, 2000);
    if (!window.__AICF_TOP_ACTION_BIND_RETRY__) {
        window.__AICF_TOP_ACTION_BIND_RETRY__ = true;
        [300, 900, 1800, 3200].forEach(function(delay) {
            setTimeout(function() {
                if (window.AICF && window.AICF.bindTopActionButtons) {
                    window.AICF.bindTopActionButtons();
                }
            }, delay);
        });
    }
    window.addEventListener('resize', function() {
        if (window.AICF && window.AICF.scheduleSegmentsPanelHeightSync) window.AICF.scheduleSegmentsPanelHeightSync();
    });
    if (window.__AICF_SEGMENTS_PANEL_OBSERVER__) {
        try { window.__AICF_SEGMENTS_PANEL_OBSERVER__.disconnect(); } catch (e) {}
        window.__AICF_SEGMENTS_PANEL_OBSERVER__ = null;
    }

    function _segmentCardForIndex(segmentIndex, element) {
        var indexText = String(segmentIndex || '');
        if (element && element.closest) {
            var ownCard = element.closest('.group-card');
            if (ownCard && String((ownCard.dataset && ownCard.dataset.segmentIndex) || '') === indexText) {
                return ownCard;
            }
        }
        return document.querySelector('.group-card[data-segment-index="' + indexText + '"]');
    }
    function _h3ConfigRoot(segmentIndex, element) {
        var modal = element && element.closest ? element.closest('.aicf-prompt-modal') : null;
        if (modal && String(modal.dataset.modelId || '') === 'minimax_h3_local_ref2va') return modal;
        return _segmentCardForIndex(segmentIndex, element);
    }
    function _h3Status(root, message, isError) {
        var target = root ? root.querySelector('.h3-config-status') : null;
        if (!target) return;
        target.textContent = message || '';
        target.classList.toggle('is-error', !!isError);
    }
    function _h3Manifest(root) {
        var source = root ? root.querySelector('.h3-asset-list') : null;
        if (!source || !source.dataset.manifest) throw new Error('参考素材尚未完整读取，未提交保存；当前稿已保留。');
        try {
            var parsed = JSON.parse(source.dataset.manifest);
            if (!Array.isArray(parsed)) throw new Error('参考素材清单格式无效');
            return parsed;
        } catch (error) {
            throw new Error('参考素材清单读取失败，未提交保存；当前稿已保留。');
        }
    }
    window.AICF.h3ManifestSignature = function(items) {
        var keys = ['asset_kind','identity_id','role_id','image_id','scene_id','background_id',
                    'voice_only','voice_disabled','voice_usage','voice_speaker','voice_asset_id'];
        return JSON.stringify(items.map(function(item) {
            var metadata = {};
            keys.forEach(function(key) { if (item.metadata && item.metadata[key] !== undefined) metadata[key] = item.metadata[key]; });
            return {id: item.asset_id || item.id || '', type:item.type, path:item.path || item.relative_path || '',
                    role:item.role || '', use_video_audio:!!item.use_video_audio, metadata:metadata};
        }));
    };
    window.AICF.hydrateH3ModalConfig = function(panel, card, segmentIndex) {
        var source = card.querySelector('.h3-card-config-source');
        var target = panel.querySelector('.h3-asset-list');
        if (!source || !target || String(source.dataset.segmentIndex || '') !== String(segmentIndex)) {
            throw new Error('本段参考配置尚未同步，请刷新页面后重新打开。');
        }
        // Copy even an empty list; never fall back to a previous page's list.
        var manifest = JSON.parse(source.dataset.manifest);
        if (!Array.isArray(manifest)) throw new Error('本段参考配置无效，请刷新页面后重新打开。');
        target.dataset.manifest = JSON.stringify(manifest);
        target.dataset.baseRevision = source.dataset.baseRevision || '';
        target.dataset.segmentUid = source.dataset.segmentUid || '';
        target.dataset.segmentIndex = String(segmentIndex);
        var regenerate = panel.querySelector('.h3-config-actions button');
        if (regenerate) {
            regenerate.removeAttribute('onclick');
            regenerate.onclick = function() { return window.AICF.regenerateH3Segment(String(segmentIndex), regenerate); };
        }
    };
    function _scheduleH3ConfigAutosave(root) {
        if (!root || window.AICF.archiveReadOnly) return;
        if (root.closest('.aicf-prompt-modal')?._editorDraft) return;
        var saveRoot = root.closest ? (root.closest('.aicf-prompt-modal') || root) : root;
        if (saveRoot._shotEditor && saveRoot._shotEditor.dirty) {
            _h3Status(saveRoot, 'Shot 草稿尚未保存，参考配置将随顶部保存一起检查。', false);
            return;
        }
        if (saveRoot._h3AutosaveTimer) window.clearTimeout(saveRoot._h3AutosaveTimer);
        var segmentIndex = String((saveRoot.dataset && saveRoot.dataset.segmentIndex) || '');
        if (!segmentIndex) {
            var card = saveRoot.closest ? saveRoot.closest('.group-card[data-segment-index]') : null;
            segmentIndex = card && card.dataset ? String(card.dataset.segmentIndex || '') : '';
        }
        if (!segmentIndex) return;
        var saveSession = String((saveRoot.dataset && saveRoot.dataset.h3Session) || '');
        _h3Status(saveRoot, '参考绑定正在自动保存…', false);
        saveRoot._h3AutosaveTimer = window.setTimeout(function() {
            saveRoot._h3AutosaveTimer = null;
            if (
                String((saveRoot.dataset && saveRoot.dataset.segmentIndex) || '') !== segmentIndex
                || String((saveRoot.dataset && saveRoot.dataset.h3Session) || '') !== saveSession
            ) {
                return;
            }
            var previous = window.AICF.h3AutosavePending;
            var pending = Promise.resolve(previous).catch(function() {}).then(function() {
                if (String(saveRoot.dataset.segmentIndex || '') !== segmentIndex || String(saveRoot.dataset.h3Session || '') !== saveSession) return;
                if (saveRoot._editorDraft || saveRoot._editorBusy || saveRoot._promptTaskBusy || saveRoot._sourceReplanPending || saveRoot._shotEditor?.dirty) return;
                return window.AICF.saveH3GenerationConfig(segmentIndex, null, saveRoot);
            });
            window.AICF.h3AutosavePending = pending;
            pending.catch(function(error) {
                _h3Status(saveRoot, error && error.message ? error.message : '参考绑定自动保存失败，请点击顶部“保存”重试。', true);
            }).finally(function() {
                if (window.AICF.h3AutosavePending === pending) window.AICF.h3AutosavePending = null;
            });
        }, 120);
    }
    window.AICF.scheduleH3ConfigAutosave = _scheduleH3ConfigAutosave;
    window.AICF.readH3Manifest = _h3Manifest;
    function _h3MoveImageAsset(root, assetId, direction) {
        if (!root || window.AICF.archiveReadOnly) return false;
        var manifest = _h3Manifest(root);
        var imageManifestIndices = [];
        manifest.forEach(function(item, manifestIndex) {
            if (String(item && item.type || '').toLowerCase() === 'image') {
                imageManifestIndices.push(manifestIndex);
            }
        });
        var currentImageIndex = imageManifestIndices.findIndex(function(manifestIndex) {
            var item = manifest[manifestIndex] || {};
            return String(item.id || item.asset_id || '') === String(assetId || '');
        });
        var targetImageIndex = currentImageIndex + Number(direction || 0);
        if (currentImageIndex < 0 || targetImageIndex < 0 || targetImageIndex >= imageManifestIndices.length) {
            return false;
        }
        var currentManifestIndex = imageManifestIndices[currentImageIndex];
        var targetManifestIndex = imageManifestIndices[targetImageIndex];
        var movedItem = manifest[currentManifestIndex];
        manifest[currentManifestIndex] = manifest[targetManifestIndex];
        manifest[targetManifestIndex] = movedItem;
        manifest.forEach(function(item, index) {
            if (item && typeof item === 'object') item.order = index + 1;
        });
        var source = root.querySelector('.h3-asset-list');
        if (source) source.dataset.manifest = JSON.stringify(manifest);
        window.AICF.refreshH3AssetCount(root);
        _h3Status(root, direction < 0 ? '图片已上移，正在同步提示词并自动保存。' : '图片已下移，正在同步提示词并自动保存。', false);
        _scheduleH3ConfigAutosave(root);
        return true;
    }
    window.AICF.moveH3ImageAsset = _h3MoveImageAsset;
    function _h3EditorRoot(root) {
        return root && root.closest ? (root.closest('.aicf-prompt-modal') || root.closest('.group-card') || root) : root;
    }
    // Replacement keeps the slot identity so the reconciler preserves tag numbers.
    window.AICF.h3ReplacementAsset = function(original, candidate) {
        var oldKind = (original.metadata || {}).asset_kind;
        var newKind = (candidate.metadata || {}).asset_kind;
        var changesKind = oldKind && newKind && oldKind !== newKind;
        var next = Object.assign({}, candidate, {
            id: String(original.id || original.asset_id),
            asset_id: String(original.id || original.asset_id),
            order: original.order,
            role: changesKind ? (candidate.role || candidate.label || '') : (original.role || candidate.role || ''),
            metadata: Object.assign({}, candidate.metadata || {})
        });
        ['asset_kind', 'identity_id', 'role_id', 'background_id', 'scene_id'].forEach(function(key) {
            if (!changesKind && original.metadata && original.metadata[key]) next.metadata[key] = original.metadata[key];
        });
        return next;
    };
    window.AICF.beginH3ImageReplacement = function(root, assetId) {
        root = _h3EditorRoot(root);
        if (!root || window.AICF.archiveReadOnly) return;
        root._h3Replacement = {assetId: assetId, session: String(root.dataset.h3Session || '')};
        window.AICF.renderH3SelectedPreview(root);
        var box = root.querySelector('.h3-replace-box');
        if (box) box.scrollIntoView({block: 'nearest'});
    };
    function _renderH3Replacement(root, container) {
        var pending = root._h3Replacement;
        if (!pending || pending.session !== String(root.dataset.h3Session || '')) return;
        var box = document.createElement('div');
        box.className = 'h3-replace-box';
        var label = document.createElement('p');
        label.setAttribute('role', 'status');
        label.textContent = pending.candidate ? '新图片：' + String(pending.candidate.label || '上传图片') : '请选择上方素材库图片，或在这里上传。保留原编号；跨人物与场景类型替换时，同步新素材的用途和名称。';
        box.appendChild(label);
        if (pending.error) {
            var errorText = document.createElement('p');
            errorText.setAttribute('role', 'alert');
            errorText.textContent = pending.error;
            box.appendChild(errorText);
        }
        if (pending.candidate) {
            var preview = document.createElement('img');
            preview.className = 'h3-preview-thumb';
            preview.src = window.AICF.assetFileUrl(pending.candidate.path);
            preview.alt = '待替换图片';
            box.appendChild(preview);
        }
        var file = document.createElement('input');
        file.type = 'file'; file.accept = 'image/*';
        file.setAttribute('aria-label', '上传替换图片');
        file.disabled = !!pending.busy;
        file.hidden = true;
        var uploadButton = document.createElement('button');
        uploadButton.type = 'button'; uploadButton.className = 'card-regen-btn action-utility';
        uploadButton.textContent = '上传图片';
        uploadButton.disabled = !!pending.busy;
        uploadButton.onclick = function() { file.click(); };
        box.appendChild(uploadButton);
        [['从人物选择', '.h3-project-character-images'], ['从背景选择', '.h3-project-background-images']].forEach(function(choice) {
            var picker = document.createElement('button');
            picker.type = 'button'; picker.className = 'card-regen-btn action-utility';
            picker.textContent = choice[0]; picker.disabled = !!pending.busy;
            picker.onclick = function() {
                var grid = root.querySelector(choice[1]);
                if (!grid) { _h3Status(root, '素材列表尚未加载，请稍后重试。', true); return; }
                var details = grid.closest('details');
                if (details) details.open = true;
                grid.scrollIntoView({block: 'start'});
                var first = grid.querySelector('button:not(:disabled)');
                if (first) first.focus({preventScroll: true});
                _h3Status(root, '正在选择替换图片：点击上方素材后，可预览并确认替换。', false);
            };
            box.appendChild(picker);
        });
        file.onchange = async function() {
            if (!file.files.length) return;
            var selectedFile = file.files[0];
            var uploadIndex = String(root.dataset.segmentIndex || '');
            pending.error = '';
            pending.busy = true;
            window.AICF.renderH3SelectedPreview(root);
            try {
                if (!/^[1-9][0-9]*$/.test(uploadIndex)) throw new Error('未找到当前分段，请关闭编辑器后重新打开');
                var form = new FormData();
                form.append('asset_type', 'image'); form.append('files', selectedFile);
                var response = await _requestJson('/api/segment/' + uploadIndex + '/assets', {method: 'POST', body: form});
                if (root._h3Replacement !== pending || pending.session !== String(root.dataset.h3Session || '')) return;
                if (!response.assets || response.assets.length !== 1) throw new Error('图片上传失败，请重试');
                pending.candidate = response.assets[0];
            } catch (error) {
                pending.error = error.message || '图片上传失败，原图已保留';
                _h3Status(root, pending.error, true);
            } finally {
                pending.busy = false;
                window.AICF.renderH3SelectedPreview(root);
            }
        };
        box.appendChild(file);
        var confirm = document.createElement('button');
        confirm.type = 'button'; confirm.className = 'card-regen-btn action-utility';
        confirm.textContent = pending.busy ? '处理中…' : '确认替换';
        confirm.disabled = !pending.candidate || !!pending.busy;
        confirm.onclick = async function() {
            if (root._h3Replacement !== pending || pending.busy) return;
            pending.busy = true;
            var originalManifest;
            try {
                if (window.AICF.h3AutosavePending) await window.AICF.h3AutosavePending;
                if (root._h3SavePending) await root._h3SavePending;
                if (pending.session !== String(root.dataset.h3Session || '')) return;
                originalManifest = _h3Manifest(root);
                var index = originalManifest.findIndex(function(item) { return String(item.id || item.asset_id) === pending.assetId; });
                if (index < 0) throw new Error('原图片已变动，请重新选择替换位置');
                var duplicate = _h3ProjectManifestIndex(originalManifest, '', pending.candidate.path);
                if (duplicate >= 0 && duplicate !== index) throw new Error('该图片已在其他参考位置，请选择另一张图片');
                var manifest = originalManifest.slice();
                manifest[index] = window.AICF.h3ReplacementAsset(manifest[index], pending.candidate);
                root.querySelector('.h3-asset-list').dataset.manifest = JSON.stringify(manifest);
                window.AICF.refreshH3AssetCount(root);
                var result = await window.AICF.saveH3GenerationConfig(root.dataset.segmentIndex, confirm, root);
                if (!result) throw new Error('保存未完成，请重新打开编辑器');
                root._h3Replacement = null;
                _h3Status(root, '参考图已原位替换，素材用途与提示词已同步。', false);
            } catch (error) {
                if (originalManifest && pending.session === String(root.dataset.h3Session || '')) {
                    root.querySelector('.h3-asset-list').dataset.manifest = JSON.stringify(originalManifest);
                    window.AICF.refreshH3AssetCount(root);
                }
                pending.error = error.message || '替换失败，原图已保留';
                _h3Status(root, pending.error, true);
            } finally {
                pending.busy = false;
                window.AICF.renderH3SelectedPreview(root);
            }
        };
        box.appendChild(confirm);
        var cancel = document.createElement('button');
        cancel.type = 'button'; cancel.className = 'card-regen-btn action-utility'; cancel.textContent = '取消替换';
        cancel.disabled = !!pending.busy;
        cancel.onclick = function() { root._h3Replacement = null; window.AICF.renderH3SelectedPreview(root); };
        box.appendChild(cancel);
        container.appendChild(box);
    }
    function _h3ProjectDisplayName(root, item) {
        var data = root._h3ProjectMaterials || {};
        var metadata = item.metadata || {};
        var path = _h3ComparablePath(item.path);
        var characters = Array.isArray(data.characters) ? data.characters : [];
        var character = characters.find(function(entry) {
            return (metadata.identity_id && String(entry.asset_id) === String(metadata.identity_id)) ||
                (path && (entry.images || []).some(function(image) {
                    return _h3ComparablePath(image.image_path) === path;
                }));
        });
        if (character && character.display_name) return String(character.display_name);
        var backgrounds = Array.isArray(data.backgrounds) ? data.backgrounds : [];
        var background = backgrounds.find(function(entry) {
            return (metadata.background_id && String(entry.asset_id) === String(metadata.background_id)) ||
                (path && _h3ComparablePath(entry.image_path) === path);
        });
        return String(background && background.display_name || item.label || '未命名图片');
    }
    window.AICF.h3BindCharacterAudio = function(manifest, imageId, audio) {
        var image = manifest.find(function(a) { return a.type === 'image' && String(a.id || a.asset_id || '') === String(imageId); });
        var identity = image && (image.metadata || {}).identity_id;
        if (!identity) throw new Error('该图片没有人物身份，无法绑定音色。');
        var result = manifest.filter(function(a) {
            return !(a.type === 'audio' && ((a.metadata || {}).identity_id === identity ||
                String(a.id || a.asset_id || '') === String(audio.id || audio.asset_id || '')));
        }).map(function(a) {
            if (a.type !== 'image' || (a.metadata || {}).identity_id !== identity) return a;
            return Object.assign({}, a, {metadata: Object.assign({}, a.metadata, {voice_disabled: false})});
        });
        if (result.filter(function(a) { return a.type === 'audio'; }).length >= 3 || result.length >= 12)
            throw new Error('参考音频最多 3 个，参考文件合计最多 12 个，请先移除一项。');
        result.push(Object.assign({}, audio, {source: 'segment_upload',
            role: '仅用于' + String(image.label || image.role || '') + '的音色参考；台词使用当前剧情，不复述参考录音',
            metadata: Object.assign({}, audio.metadata || {}, {identity_id: identity,
                role_id: (image.metadata || {}).role_id, binding_source: 'segment_character_voice', voice_only: true})}));
        return result;
    };
    function _h3CharacterVoiceControls(root, item, info, manifest) {
        var identity = (item.metadata || {}).identity_id;
        if (!identity) return;
        info.dataset.voiceIdentity = identity;
        var box = document.createElement('div'); box.className = 'h3-character-voice-controls';
        var title = document.createElement('strong'); title.textContent = '本段参考音色'; box.appendChild(title);
        if ((item.metadata || {}).library_voice || (item.metadata || {}).voice_disabled || manifest.some(function(a) { return a.type === 'audio' && (a.metadata || {}).identity_id === identity && (a.metadata || {}).binding_source === 'character_voice'; })) {
            var toggleLabel = document.createElement('label');
            var toggle = document.createElement('input'); toggle.type = 'checkbox';
            toggle.checked = !(item.metadata || {}).voice_disabled; toggle.disabled = !!window.AICF.archiveReadOnly;
            toggle.onchange = function() {
                var m = _h3Manifest(root);
                m.forEach(function(a) { if (a.type === 'image' && (a.metadata || {}).identity_id === identity)
                    a.metadata = Object.assign({}, a.metadata || {}, {voice_disabled: !toggle.checked}); });
                root.querySelector('.h3-asset-list').dataset.manifest = JSON.stringify(m);
                _scheduleH3ConfigAutosave(root);
            };
            toggleLabel.appendChild(toggle); toggleLabel.appendChild(document.createTextNode('自动使用人物默认音色'));
            box.appendChild(toggleLabel);
        }

        var select = document.createElement('select'); select.setAttribute('aria-label', String(item.label || '') + '选择已有参考音频');
        select.add(new Option('选择已有参考音频…', ''));
        manifest.forEach(function(a, i) {
            if (a.type !== 'audio') return;
            select.add(new Option(a.label || '参考音频', String(i)));
            if ((a.metadata || {}).identity_id === identity && (a.metadata || {}).voice_only)
                select.value = String(i);
        });
        select.disabled = !!window.AICF.archiveReadOnly;
        function commit(audio) {
            var source = root.querySelector('.h3-asset-list');
            source.dataset.manifest = JSON.stringify(window.AICF.h3BindCharacterAudio(_h3Manifest(root), item.id || item.asset_id, audio));
            window.AICF.refreshH3AssetCount(root);
            _scheduleH3ConfigAutosave(root);
            _h3Status(root, '已绑定本段人物音色，正在自动保存。', false);
        }
        select.onchange = function() { if (select.value === '' || window.AICF.archiveReadOnly) return; try { commit(manifest[Number(select.value)]); } catch (e) { _h3Status(root, e.message, true); } };
        box.appendChild(select);
        var label = document.createElement('label'); label.textContent = '上传 / 替换音频';
        var upload = document.createElement('input'); upload.type = 'file'; upload.accept = '.mp3,.wav';
        upload.setAttribute('aria-label', String(item.label || '') + '上传或替换参考音频');
        upload.disabled = !!window.AICF.archiveReadOnly;
        upload.onchange = async function() {
            if (!upload.files || !upload.files.length || window.AICF.archiveReadOnly) return;
            if (upload.files[0].size > 15 * 1024 * 1024) { _h3Status(root, '参考音色不能超过 15 MB', true); upload.value = ''; return; }
            var session = String(root.dataset.h3Session || '');
            var runtime = String(window.AICF.promptRuntimeRevision || 0);
            var segment = String(root.dataset.segmentIndex || (root.querySelector('.h3-asset-list') || {}).dataset?.segmentIndex || '');
            upload.disabled = true;
            try {
                var form = new FormData(); form.append('asset_type', 'audio'); form.append('files', upload.files[0]);
                _h3Status(root, '正在上传并校验人物参考音频…', false);
                var data = await _requestJson('/api/segment/' + segment + '/assets', {method: 'POST', body: form});
                if (String(root.dataset.h3Session || '') !== session || String(root.dataset.segmentIndex || segment) !== segment ||
                    String(window.AICF.promptRuntimeRevision || 0) !== runtime || !root.isConnected) return;
                if (!(data.assets || []).length) throw new Error('未返回有效参考音频');
                var duration = Number((data.assets[0].metadata || {}).duration_sec);
                if (!Number.isFinite(duration) || duration < 2 || duration > 15) throw new Error('人物参考音色需为 2–15 秒的有效音频');
                commit(data.assets[0]);
            } catch (e) { _h3Status(root, e.message, true); }
            finally { upload.disabled = !!window.AICF.archiveReadOnly; upload.value = ''; }
        };
        label.appendChild(upload); box.appendChild(label); info.appendChild(box);
    }
    window.AICF.renderH3SelectedPreview = function(root) {
        root = _h3EditorRoot(root);
        if (!root) return;
        var container = root.querySelector('.h3-selected-preview');
        if (!container) return;
        var manifest = _h3Manifest(root);
        container.replaceChildren();
        if (!manifest.length) return;
        var title = document.createElement('div');
        title.className = 'aicf-material-panel-title';
        title.textContent = '已选参考素材（按顺序排列）';
        container.appendChild(title);
        _renderH3Replacement(root, container);
        var list = document.createElement('ol');
        list.className = 'h3-selected-preview-list';
        var imageIndex = 0;
        manifest.forEach(function(item, idx) {
            var itemType = String(item && item.type || 'image').toLowerCase();
            if (itemType !== 'image') return;
            item = Object.assign({}, item, {label: _h3ProjectDisplayName(root, item)});
            imageIndex += 1;
            var pictureNumber = imageIndex;
            var li = document.createElement('li');
            var assetId = String(item && (item.id || item.asset_id) || '');
            var orderControl = document.createElement('span');
            orderControl.className = 'h3-preview-order';
            var num = document.createElement('span');
            num.className = 'h3-preview-num';
            num.textContent = String(imageIndex);
            num.setAttribute('aria-label', '当前顺序 ' + String(imageIndex));
            var moveGroup = document.createElement('span');
            moveGroup.className = 'h3-preview-move-group';
            var moveUp = document.createElement('button');
            moveUp.type = 'button';
            moveUp.className = 'h3-preview-move';
            moveUp.title = '上移';
            moveUp.setAttribute('aria-label', '上移 ' + String(item.label || '图片'));
            moveUp.disabled = imageIndex === 1 || !!window.AICF.archiveReadOnly;
            moveUp.innerHTML = '<svg viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M4 10l4-4 4 4"/></svg>';
            moveUp.addEventListener('click', (function(id) {
                return function() { _h3MoveImageAsset(root, id, -1); };
            })(assetId));
            var moveDown = document.createElement('button');
            moveDown.type = 'button';
            moveDown.className = 'h3-preview-move';
            moveDown.title = '下移';
            moveDown.setAttribute('aria-label', '下移 ' + String(item.label || '图片'));
            moveDown.disabled = imageIndex === manifest.filter(function(value) {
                return String(value && value.type || '').toLowerCase() === 'image';
            }).length || !!window.AICF.archiveReadOnly;
            moveDown.innerHTML = '<svg viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M4 6l4 4 4-4"/></svg>';
            moveDown.addEventListener('click', (function(id) {
                return function() { _h3MoveImageAsset(root, id, 1); };
            })(assetId));
            moveGroup.appendChild(moveUp);
            moveGroup.appendChild(moveDown);
            orderControl.appendChild(num);
            orderControl.appendChild(moveGroup);
            var thumb = document.createElement('img');
            thumb.className = 'h3-preview-thumb';
            thumb.src = window.AICF.assetFileUrl(String(item.path || ''));
            thumb.alt = String(item.label || '图片');
            thumb.loading = 'lazy';
            var info = document.createElement('span');
            info.className = 'h3-preview-info';
            var nameEl = document.createElement('strong');
            nameEl.textContent = String(item.label || '未命名图片');
            var usage = document.createElement('small');
            usage.textContent = _h3ImageUsageLabel(item);
            info.appendChild(nameEl);
            info.appendChild(usage);
            var roleInput = document.createElement('input');
            roleInput.type = 'text'; roleInput.className = 'h3-asset-role';
            roleInput.dataset.assetId = assetId; roleInput.value = String(item.role || '');
            roleInput.setAttribute('aria-label', String(item.label || '图片') + '参考名称或用途');
            roleInput.disabled = !!window.AICF.archiveReadOnly;
            roleInput.onchange = function() { window.AICF.setH3AssetRole(String(root.dataset.segmentIndex || ''), roleInput); };
            var details = document.createElement('div'); details.className = 'h3-character-details';
            details.appendChild(roleInput);
            _h3CharacterVoiceControls(root, item, details, manifest);
            var removeBtn = document.createElement('button');
            removeBtn.type = 'button';
            removeBtn.className = 'card-regen-btn action-utility h3-preview-remove';
            removeBtn.textContent = '移除';
            removeBtn.setAttribute('aria-label', '移除 ' + String(item.label || '图片'));
            removeBtn.addEventListener('click', (function(index) {
                return function() {
                    if (window.AICF.archiveReadOnly) return;
                    var current = _h3Manifest(root)[index] || {};
                    if (window.AICF.editH3Reference && root.closest('.aicf-prompt-modal')) { window.AICF.editH3Reference(root, current.asset_id || current.id); return; }
                    var removeName = String(current.role || current.label || '该图片');
                    var promptField = root.querySelector('.h3-prompt-override, .aicf-prompt-modal-body');
                    if (promptField && String(promptField.value || '').indexOf('<Subject ' + String(pictureNumber) + '>') >= 0) {
                        var approved = window.confirm('“' + removeName + '”仍可能在右侧镜头中使用。移除后会标记未解决引用，并在修正前阻止生成。确定移除吗？');
                        if (!approved) return;
                    }
                    var m = _h3Manifest(root);
                    m.splice(index, 1);
                    var removedIdentity = (current.metadata || {}).identity_id;
                    var source = root.querySelector('.h3-asset-list');
                    if (source) source.dataset.manifest = JSON.stringify(m);
                    window.AICF.renderH3SelectedPreview(root);
                    window.AICF.refreshH3AssetCount(root);
                    _h3Status(root, '已移除图片，正在自动保存。', false);
                    _scheduleH3ConfigAutosave(root);
                };
            })(idx));
            li.appendChild(orderControl);
            li.appendChild(thumb);
            li.appendChild(info);
            var actions = document.createElement('span');
            actions.className = 'h3-preview-actions';
            var replaceBtn = document.createElement('button');
            replaceBtn.type = 'button'; replaceBtn.className = 'card-regen-btn action-utility';
            replaceBtn.textContent = '替换';
            replaceBtn.setAttribute('aria-label', '替换图 ' + pictureNumber + ' ' + String(item.label || ''));
            replaceBtn.disabled = !!window.AICF.archiveReadOnly || !!(root._h3Replacement && root._h3Replacement.busy);
            replaceBtn.onclick = function() { window.AICF.beginH3ImageReplacement(root, assetId); };
            actions.appendChild(replaceBtn);
            actions.appendChild(removeBtn);
            var identity = (item.metadata || {}).identity_id;
            if (identity) {
                var voice = (item.metadata || {}).library_voice;
                var alreadyBound = manifest.some(function(a) {
                    return a.type === 'audio' && (a.metadata || {}).identity_id === identity;
                });
                var voiceBtn = document.createElement('button');
                voiceBtn.type = 'button'; voiceBtn.className = 'card-regen-btn action-utility';
                voiceBtn.textContent = alreadyBound ? '音色已添加' : voice ? '添加参考音色' : '未设置参考音色';
                voiceBtn.disabled = !!window.AICF.archiveReadOnly || alreadyBound || !voice;
                voiceBtn.setAttribute('aria-label', String(item.label || item.role || '') + ' ' + voiceBtn.textContent);
                voiceBtn.onclick = function() {
                    var m = _h3Manifest(root);
                    if (m.some(function(a) { return a.type === 'audio' && (a.metadata || {}).identity_id === identity; })) return;
                    if (m.filter(function(a) { return a.type === 'audio'; }).length >= 3 || m.length >= 12) {
                        _h3Status(root, '参考音频最多 3 个，参考文件合计最多 12 个，请先移除一项。', true);
                        return;
                    }
                    m.push({asset_id: 'voice_' + identity, type: 'audio', path: voice.path,
                        label: String(item.label || item.role || '') + '参考音色',
                        role: '仅用于' + String(item.label || item.role || '') + '的音色参考，台词使用当前剧情',
                        source: 'character_library', metadata: {identity_id: identity,
                            role_id: (item.metadata || {}).role_id, binding_source: 'manual_character_voice',
                            voice_only: true, duration_sec: voice.duration_sec}});
                    var source = root.querySelector('.h3-asset-list');
                    if (source) source.dataset.manifest = JSON.stringify(m);
                    window.AICF.renderH3SelectedPreview(root);
                    window.AICF.renderH3AssetRows(root);
                    window.AICF.refreshH3AssetCount(root);
                    _h3Status(root, '已添加人物参考音色，正在自动保存。', false);
                    _scheduleH3ConfigAutosave(root);
                };
                actions.appendChild(voiceBtn);
            }
            li.appendChild(actions);
            li.appendChild(details);
            list.appendChild(li);
        });
        container.appendChild(list);
        if (window.AICF.renderH3AssetRows) window.AICF.renderH3AssetRows(root);
    };
    window.AICF.renderH3AssetRows = function(root) {
        if (!root) return;
        var source = root.querySelector('.h3-asset-list:not(.h3-card-config-source)');
        var list = source ? source.querySelector('ul') : null;
        if (!source || !list) return;
        var segmentIndex = String(source.dataset.segmentIndex || (root.dataset && root.dataset.segmentIndex) || '');
        var manifest = _h3Manifest(root);
        list.replaceChildren();
        root.querySelectorAll('.h3-bound-audio').forEach(function(row) { row.remove(); });
        if (!manifest.length) {
            var empty = document.createElement('li');
            empty.className = 'muted';
            empty.textContent = '尚未选择参考素材。';
            list.appendChild(empty);
            return;
        }
        manifest.forEach(function(item) {
            var assetId = String(item && (item.id || item.asset_id) || '');
            var itemType = String(item && item.type || 'image').toLowerCase();
            if (itemType === 'image') return;
            var row = document.createElement('div');
            var name = document.createElement('span');
            name.textContent = itemType + ' · ' + String(item.label || item.path || assetId);
            var role = document.createElement('input');
            role.type = 'text';
            role.className = 'h3-asset-role';
            role.dataset.assetId = assetId;
            role.value = String(item.role || '');
            role.placeholder = '参考名称或用途（可选）';
            role.setAttribute('aria-label', String(item.label || assetId) + ' 的参考名称或用途');
            role.addEventListener('change', function() { window.AICF.setH3AssetRole(segmentIndex, role); });
            row.appendChild(name);
            row.appendChild(role);
            if (itemType === 'audio') {
                var player = document.createElement('audio');
                player.controls = true; player.preload = 'none';
                player.style.width = '100%'; player.style.minWidth = '0';
                player.src = '/gradio_api/file=' + String(item.path || '').replace(/\\/g, '/');
                player.setAttribute('aria-label', '试听 ' + String(item.label || '参考音频'));
                row.appendChild(player);
                var detail = document.createElement('small');
                detail.textContent = ((item.metadata || {}).binding_source === 'character_voice' ? '人物素材库自动带入' : '手动上传') + ' · ' + String((item.metadata || {}).duration_sec || '?') + ' 秒';
                row.appendChild(detail);
                if ((item.metadata || {}).binding_source !== 'character_voice') {
                    var owner = document.createElement('select');
                    owner.setAttribute('aria-label', '音频绑定人物');
                    owner.add(new Option('通用音频参考（不绑定人物）', ''));
                    owner.add(new Option('旁白（不需要人物图片）', '__narration'));
                    owner.add(new Option('电话另一端（不需要人物图片）', '__telephone'));
                    var seen = new Set();
                    manifest.forEach(function(image) {
                        var identity = (image.metadata || {}).identity_id;
                        if (image.type === 'image' && identity && !seen.has(identity)) {
                            seen.add(identity); owner.add(new Option(image.label || image.role || identity, identity));
                        }
                    });
                    owner.value = (item.metadata || {}).voice_usage ? '__' + item.metadata.voice_usage : (item.metadata || {}).identity_id || '';
                    owner.disabled = !!window.AICF.archiveReadOnly;
                    owner.addEventListener('change', function() {
                        item.metadata = Object.assign({}, item.metadata || {});
                        delete item.metadata.voice_usage;
                        delete item.metadata.voice_speaker;
                        if (owner.value === '__narration' || owner.value === '__telephone') {
                            delete item.metadata.identity_id;
                            item.metadata.voice_usage = owner.value.slice(2);
                            item.metadata.voice_only = true;
                            if (!String(item.role || '').trim()) {
                                item.role = item.metadata.voice_usage === 'narration' ? '旁白' : '电话另一端';
                                role.value = item.role;
                            }
                        } else if (owner.value) {
                            item.metadata.identity_id = owner.value; item.metadata.voice_only = true;
                        } else {
                            delete item.metadata.identity_id; delete item.metadata.voice_only;
                        }
                        source.dataset.manifest = JSON.stringify(manifest);
                        window.AICF.refreshH3AssetCount(root);
                        _scheduleH3ConfigAutosave(_h3EditorRoot(root));
                    });
                    row.appendChild(owner);
                }
            }
            if (itemType === 'video') {
                var audioLabel = document.createElement('label');
                audioLabel.className = 'h3-video-audio-toggle';
                var audio = document.createElement('input');
                audio.type = 'checkbox';
                audio.dataset.assetId = assetId;
                audio.checked = !!item.use_video_audio;
                audio.addEventListener('change', function() { window.AICF.setH3VideoAudio(segmentIndex, audio); });
                audioLabel.appendChild(audio);
                audioLabel.appendChild(document.createTextNode('使用视频内嵌音频'));
                row.appendChild(audioLabel);
            }
            var remove = document.createElement('button');
            remove.type = 'button';
            remove.className = 'card-regen-btn action-utility';
            remove.dataset.assetId = assetId;
            remove.textContent = '移除';
            remove.setAttribute('aria-label', '移除 ' + String(item.label || assetId));
            remove.addEventListener('click', function() { window.AICF.removeH3Asset(segmentIndex, remove); });
            row.appendChild(remove);
            var voiceHost = Array.from(root.querySelectorAll('[data-voice-identity]')).find(function(host) {
                return host.dataset.voiceIdentity === String((item.metadata || {}).identity_id || '');
            });
            if (itemType === 'audio' && voiceHost) {
                row.className = 'h3-bound-audio'; voiceHost.appendChild(row);
            } else { var entry = document.createElement('li'); entry.appendChild(row); list.appendChild(entry); }
        });
    };
    function _h3ImageUsageLabel(item) {
        var metadata = item && item.metadata && typeof item.metadata === 'object' ? item.metadata : {};
        var assetKind = String(metadata.asset_kind || '').toLowerCase();
        if (assetKind === 'background' || metadata.background_id || metadata.scene_id) {
            return '场景参考（锁场景）';
        }
        var label = String(item.label || item.role || '').toLowerCase();
        if (label.indexOf('背景') >= 0 || label.indexOf('场景') >= 0 || label.indexOf('环境') >= 0) {
            return '场景参考（锁场景）';
        }
        return '人物参考（锁脸）';
    }
    window.AICF.refreshH3AssetCount = function(root) {
        if (window.AICF.scheduleH3PromptCount) window.AICF.scheduleH3PromptCount(root);
        if (!root) return;
        var manifest = _h3Manifest(root);
        var counts = {image: 0, video: 0, audio: 0};
        manifest.forEach(function(item) {
            var type = String(item && item.type || '').toLowerCase();
            if (Object.prototype.hasOwnProperty.call(counts, type)) counts[type] += 1;
        });
        var target = root.querySelector('[data-h3-count]');
        if (target) {
            target.textContent = '图片 ' + counts.image + '/9 · 视频 ' + counts.video + '/3 · 音频 ' + counts.audio + '/3 · 合计 ' + manifest.length + '/12。音频需至少搭配一项图片或视频参考。';
        }
        if (window.AICF.syncH3ProjectImageState) window.AICF.syncH3ProjectImageState(root);
        if (window.AICF.renderH3SelectedPreview) window.AICF.renderH3SelectedPreview(root);
        if (window.AICF.renderH3AssetRows) window.AICF.renderH3AssetRows(root);
    };
    function _h3ComparablePath(value) {
        return String(value || '').replace(/\\/g, '/').toLowerCase();
    }
    function _h3ProjectManifestIndex(manifest, assetId, path) {
        var comparablePath = _h3ComparablePath(path);
        return manifest.findIndex(function(item) {
            if (String(item && item.type || '').toLowerCase() !== 'image') return false;
            var itemId = String(item && (item.id || item.asset_id) || '');
            return comparablePath && item.path ? _h3ComparablePath(item.path) === comparablePath : (assetId && itemId === assetId);
        });
    }
    window.AICF.syncH3ProjectImageState = function(root) {
        if (!root) return;
        var manifest = _h3Manifest(root);
        root.querySelectorAll('.h3-project-image-option').forEach(function(option) {
            var selected = _h3ProjectManifestIndex(
                manifest,
                String(option.dataset.assetId || ''),
                String(option.dataset.assetPath || '')
            ) >= 0;
            option.classList.toggle('is-selected', selected);
            option.classList.toggle('is-muted', !selected);
            option.setAttribute('aria-pressed', selected ? 'true' : 'false');
            var state = option.querySelector('.aicf-material-option-state');
            if (state) state.textContent = option.disabled ? '不可用' : (selected ? '已选为 H3 图片参考' : '未选择');
        });
    };
    function _createH3ProjectImageOption(root, item) {
        var option = document.createElement('button');
        option.type = 'button';
        option.className = 'aicf-material-option h3-project-image-option is-muted';
        option.dataset.assetId = String(item.asset_id || '');
        option.dataset.assetPath = String(item.path || '');
        option._h3Metadata = item.metadata && typeof item.metadata === 'object' ? item.metadata : {};
        option.disabled = !item.available || !!window.AICF.archiveReadOnly;
        option.setAttribute('aria-pressed', 'false');
        option.setAttribute('aria-label', String(item.label || '项目参考图片'));

        var media = document.createElement('span');
        media.className = 'aicf-material-option-media';
        if (item.path) {
            var image = document.createElement('img');
            image.src = window.AICF.assetFileUrl(item.path);
            image.alt = String(item.label || '项目参考图片');
            image.loading = 'lazy';
            media.appendChild(image);
        } else {
            var missing = document.createElement('span');
            missing.className = 'aicf-material-missing';
            missing.textContent = '图片缺失';
            media.appendChild(missing);
        }

        var text = document.createElement('span');
        text.className = 'aicf-material-option-text';
        var name = document.createElement('strong');
        name.textContent = String(item.label || '未命名图片');
        var meta = document.createElement('small');
        meta.textContent = String(item.kind_label || '项目图片');
        text.appendChild(name);
        text.appendChild(meta);
        var state = document.createElement('span');
        state.className = 'aicf-material-option-state';
        state.textContent = option.disabled ? '不可用' : '未选择';
        option.appendChild(media);
        option.appendChild(text);
        option.appendChild(state);
        option.addEventListener('click', function() {
            if (window.AICF.archiveReadOnly || option.disabled) return;
            var replacement = root._h3Replacement;
            if (replacement && replacement.session === String(root.dataset.h3Session || '')) {
                if (replacement.busy) return;
                replacement.candidate = {id: item.asset_id, type: 'image', path: item.path,
                    label: item.label, role: item.role, source: 'project', metadata: Object.assign({}, option._h3Metadata)};
                window.AICF.renderH3SelectedPreview(root);
                var replaceBox = root.querySelector('.h3-replace-box');
                if (replaceBox) replaceBox.scrollIntoView({block: 'nearest'});
                return;
            }
            var manifest = _h3Manifest(root);
            var assetId = String(option.dataset.assetId || '');
            var assetPath = String(option.dataset.assetPath || '');
            var existingIndex = _h3ProjectManifestIndex(manifest, assetId, assetPath);
            if (existingIndex >= 0) {
                manifest.splice(existingIndex, 1);
            } else {
                var imageCount = manifest.filter(function(entry) {
                    return String(entry && entry.type || '').toLowerCase() === 'image';
                }).length;
                if (imageCount >= 9) {
                    _h3Status(root, 'H3 图片参考最多选择 9 张。', true);
                    return;
                }
                if (manifest.length >= 12) {
                    _h3Status(root, 'H3 上传参考素材总数不能超过 12 项。', true);
                    return;
                }
                manifest.push({
                    id: assetId,
                    type: 'image',
                    path: assetPath,
                    label: String(item.label || ''),
                    role: String(item.role || item.label || ''),
                    source: 'project',
                    metadata: Object.assign({}, option._h3Metadata || {})
                });
            }
            var source = root.querySelector('.h3-asset-list');
            if (source) source.dataset.manifest = JSON.stringify(manifest);
            window.AICF.refreshH3AssetCount(root);
            _h3Status(root, existingIndex >= 0 ? '已取消项目图片，正在自动保存。' : '已选择项目图片，正在自动保存。', false);
            _scheduleH3ConfigAutosave(root);
        });
        return option;
    }
    window.AICF.renderH3ProjectImages = function(root, data) {
        root = _h3EditorRoot(root);
        if (!root) return;
        data = data && typeof data === 'object' ? data : {};
        root._h3ProjectMaterials = data;
        var characterGrid = root.querySelector('.h3-project-character-images');
        var backgroundGrid = root.querySelector('.h3-project-background-images');
        if (characterGrid) {
            characterGrid.replaceChildren();
            var characterImageCount = 0;
            (Array.isArray(data.characters) ? data.characters : []).forEach(function(character) {
                (Array.isArray(character.images) ? character.images : []).forEach(function(imageItem) {
                    characterImageCount += 1;
                    characterGrid.appendChild(_createH3ProjectImageOption(root, {
                        asset_id: 'project:character:' + String(character.id || '') + ':' + String(imageItem.id || ''),
                        path: String(imageItem.image_path || ''),
                        label: String(character.display_name || character.asset_id || character.id || '人物'),
                        kind_label: '人物图片',
                        available: !!character.available && !!imageItem.available,
                        role: String(character.display_name || ''),
                        metadata: {
                            asset_kind: 'character',
                            identity_id: String(character.asset_id || ''),
                            role_id: String(character.id || ''),
                            image_id: String(imageItem.id || ''),
                            subject_description: String(character.subject_description || imageItem.description_cn || ''),
                            image_description: String(imageItem.description_cn || '')
                        }
                    }));
                });
            });
            if (!characterImageCount) {
                var emptyCharacters = document.createElement('div');
                emptyCharacters.className = 'aicf-material-empty';
                emptyCharacters.textContent = '项目尚未选择可用人物图片';
                characterGrid.appendChild(emptyCharacters);
            }
        }
        if (backgroundGrid) {
            backgroundGrid.replaceChildren();
            var backgrounds = Array.isArray(data.backgrounds) ? data.backgrounds : [];
            backgrounds.forEach(function(background) {
                backgroundGrid.appendChild(_createH3ProjectImageOption(root, {
                    asset_id: 'project:background:' + String(background.id || background.asset_id || ''),
                    path: String(background.image_path || ''),
                    label: String(background.display_name || background.asset_id || background.id || '场景图片'),
                    kind_label: '场景图片 · 可选',
                    available: !!background.available,
                    role: String(background.display_name || ''),
                    metadata: {
                        asset_kind: 'background',
                        background_id: String(background.asset_id || ''),
                        scene_id: String(background.id || ''),
                        description_cn: String(background.description_cn || '')
                    }
                }));
            });
            if (!backgrounds.length) {
                var emptyBackgrounds = document.createElement('div');
                emptyBackgrounds.className = 'aicf-material-empty';
                emptyBackgrounds.textContent = '项目尚未选择场景图片；H3 不要求必须选择背景';
                backgroundGrid.appendChild(emptyBackgrounds);
            }
        }
        window.AICF.syncH3ProjectImageState(root);
    };
    function _validationMessage(errors) {
        return (Array.isArray(errors) ? errors : []).map(function(item) {
            if (item && typeof item === 'object') return String(item.message || item.code || JSON.stringify(item));
            return String(item || '');
        }).filter(Boolean).join('；');
    }
    async function _requestJson(url, options) {
        var response = await fetch(url, options || {});
        var data = await window.AICF.readJsonResponse(response, url);
        if (!response.ok || !data || data.ok === false) {
            throw new Error((data && data.message) || _validationMessage(data && data.errors) || '请求失败（HTTP ' + response.status + '）');
        }
        return data;
    }
    window.AICF.changeSegmentModel = function(select) {
        if (window.AICF.archiveReadOnly || !select) return false;
        var card = select.closest ? select.closest('.group-card') : null;
        var previous = String(select.dataset.currentModel || '');
        var next = String(select.value || '');
        var errorBox = card ? card.querySelector('.segment-model-error') : null;
        if (!next || next === previous) return false;
        select.disabled = true;
        if (errorBox) errorBox.textContent = '正在切换模型…';
        _requestJson('/api/segment/model', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({segment_index: Number(card && card.dataset.segmentIndex), model_id: next})
        }).then(function(response) {
            var host = document.createElement('template');
            host.innerHTML = String(response && response.segment_panel_html || '').trim();
            var replacement = host.content.querySelector('.group-card[data-segment-index="' + String(card && card.dataset.segmentIndex || '') + '"]');
            if (!card || !replacement) throw new Error('模型已保存，但分段界面更新失败；请稍后重试');
            card.replaceWith(replacement);
            var replacementSelect = replacement.querySelector('.segment-model-select');
            var feedback = replacement.querySelector('.segment-model-error');
            if (replacementSelect) {
                replacementSelect.disabled = false;
                replacementSelect.dataset.currentModel = next;
                try { replacementSelect.focus({preventScroll: true}); } catch (focusError) { replacementSelect.focus(); }
            }
            if (feedback) {
                feedback.textContent = '模型已切换';
                feedback.classList.add('is-success');
                setTimeout(function() {
                    feedback.textContent = '';
                    feedback.classList.remove('is-success');
                }, 2200);
            }
        }).catch(function(error) {
            select.disabled = false;
            select.value = previous;
            if (errorBox) errorBox.textContent = error && error.message ? error.message : '切换模型失败';
        });
        return false;
    };
    window.AICF.removeH3Asset = function(segmentIndex, button) {
        if (window.AICF.archiveReadOnly) return false;
        var root = _h3ConfigRoot(segmentIndex, button);
        var assetId = button && button.dataset ? String(button.dataset.assetId || '') : '';
        if (window.AICF.editH3Reference && root.closest('.aicf-prompt-modal')) { window.AICF.editH3Reference(root, assetId); return false; }
        var originalManifest = _h3Manifest(root);
        var removedIndex = originalManifest.findIndex(function(item) {
            return String(item && (item.id || item.asset_id) || '') === assetId;
        });
        var removedItem = removedIndex >= 0 ? originalManifest[removedIndex] : {};
        if (removedItem.type === 'audio' && (removedItem.metadata || {}).identity_id && (removedItem.metadata || {}).voice_only) {
            originalManifest.forEach(function(asset) {
                if (asset.type === 'image' && (asset.metadata || {}).identity_id === removedItem.metadata.identity_id)
                    asset.metadata = Object.assign({}, asset.metadata, {voice_disabled: true});
            });
        }
        var removedPictureIndex = originalManifest.slice(0, Math.max(0, removedIndex + 1)).filter(function(item) {
            return String(item && item.type || '').toLowerCase() === 'image';
        }).length;
        var promptField = root ? root.querySelector('.h3-prompt-override, .aicf-prompt-modal-body') : null;
        if (removedItem.type === 'image' && removedPictureIndex && promptField && String(promptField.value || '').indexOf('<Subject ' + String(removedPictureIndex) + '>') >= 0) {
            var approved = window.confirm('“' + String(removedItem.role || removedItem.label || '该图片') + '”仍可能在右侧镜头中使用。移除后会标记未解决引用，并在修正前阻止生成。确定移除吗？');
            if (!approved) return false;
        }
        var manifest = originalManifest.filter(function(item) {
            return String(item && (item.id || item.asset_id) || '') !== assetId;
        });
        var removedIdentity = (removedItem.metadata || {}).identity_id;
        var source = root ? root.querySelector('.h3-asset-list') : null;
        if (source) source.dataset.manifest = JSON.stringify(manifest);
        window.AICF.refreshH3AssetCount(root);
        _h3Status(root, '素材已从本段配置移除，正在自动保存。', false);
        _scheduleH3ConfigAutosave(root);
        return false;
    };
    window.AICF.setH3AssetRole = function(segmentIndex, input) {
        if (window.AICF.archiveReadOnly || !input) return false;
        var root = _h3ConfigRoot(segmentIndex, input);
        var assetId = input.dataset ? String(input.dataset.assetId || '') : '';
        var manifest = _h3Manifest(root);
        manifest.forEach(function(item) {
            if (String(item && (item.id || item.asset_id) || '') === assetId) {
                item.role = String(input.value || '').trim();
            }
        });
        var source = root ? root.querySelector('.h3-asset-list') : null;
        if (source) source.dataset.manifest = JSON.stringify(manifest);
        window.AICF.refreshH3AssetCount(root);
        _h3Status(root, '素材用途已修改，正在自动保存。', false);
        _scheduleH3ConfigAutosave(root);
        return false;
    };
    window.AICF.setH3VideoAudio = function(segmentIndex, input) {
        if (window.AICF.archiveReadOnly || !input) return false;
        var root = _h3ConfigRoot(segmentIndex, input);
        var assetId = input.dataset ? String(input.dataset.assetId || '') : '';
        var manifest = _h3Manifest(root);
        manifest.forEach(function(item) {
            if (String(item && (item.id || item.asset_id) || '') === assetId) {
                item.use_video_audio = !!input.checked;
            }
        });
        var source = root ? root.querySelector('.h3-asset-list') : null;
        if (source) source.dataset.manifest = JSON.stringify(manifest);
        _h3Status(root, '视频音轨设置已修改，正在自动保存。', false);
        _scheduleH3ConfigAutosave(root);
        return false;
    };
    window.AICF.showH3PromptCount = function(root, data) {
        var counter = root.querySelector('.aicf-prompt-char-count');
        if (!counter) return;
        counter.textContent = String(data.prompt_chars) + '/' + String(data.prompt_max_chars || '不限');
        counter.classList.toggle('is-over-limit', !!data.prompt_max_chars && data.prompt_chars > data.prompt_max_chars);
        counter.title = '最终提交提示词字符数 / 当前模型字符上限（包含主体定义、素材标签和音色绑定）';
    };
    window.AICF.scheduleH3PromptCount = function(root) {
        var modal = root && root.closest ? root.closest('.aicf-prompt-modal') : null;
        if (!modal || !modal.querySelector('.aicf-prompt-char-count') || modal.dataset.modelId !== 'minimax_h3_local_ref2va') return;
        window.clearTimeout(modal._promptCountTimer);
        var sequence = (modal._promptCountSequence || 0) + 1;
        modal._promptCountSequence = sequence;
        var session = modal.dataset.h3Session;
        var counter = modal.querySelector('.aicf-prompt-char-count');
        counter.textContent = '计算中…';
        counter.classList.remove('is-over-limit');
        modal._promptCountTimer = window.setTimeout(async function() {
            try {
                var text = modal.querySelector('.aicf-prompt-modal-body').value;
                var provenance = null;
                if (modal.dataset.promptProvenance && window.AICF.promptTextMatches(text, modal.dataset.promptRevisionPrompt)) {
                    provenance = JSON.parse(modal.dataset.promptProvenance);
                }
                var data = await _requestJson('/api/segment/prompt-preview', {
                    method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({segment_index: Number(modal.dataset.segmentIndex), prompt_override: text,
                        asset_manifest: _h3Manifest(modal), prompt_provenance: provenance})
                });
                if (modal._promptCountSequence === sequence && modal.dataset.h3Session === session) window.AICF.showH3PromptCount(modal, data);
            } catch (error) {
                if (modal._promptCountSequence === sequence && modal.dataset.h3Session === session) {
                    counter.textContent = '无法计算'; counter.title = error.message;
                }
            }
        }, 350);
    };
    window.AICF.saveH3GenerationConfig = async function(segmentIndex, button, rootOverride, forceSave, refreshState) {
        if (window.AICF.archiveReadOnly) return false;
        var root = rootOverride || _h3ConfigRoot(segmentIndex, button);
        if (root && root.closest) root = root.closest('.aicf-prompt-modal') || root;
        if (root?._editorBusy) throw new Error('完整编辑稿正在保存，请稍候。');
        if (root && root._shotEditor && root._shotEditor.dirty && !root._shotEditor.stale) throw new Error('请先通过顶部保存 Shot 草稿，再生成视频或单独保存配置');
        var requestedIndex = String(segmentIndex || '');
        var rootIndex = root && root.dataset ? String(root.dataset.segmentIndex || '') : '';
        if (root && root.classList && root.classList.contains('aicf-prompt-modal')) {
            if (!rootIndex || rootIndex !== requestedIndex) {
                throw new Error('编辑分段已切换，已取消旧分段的保存请求。');
            }
            segmentIndex = rootIndex;
        }
        var card = _segmentCardForIndex(segmentIndex, button);
        if (!card || !root) return false;
        var saveSession = String((root.dataset && root.dataset.h3Session) || '');
        var saveRuntime = String(window.AICF.promptRuntimeRevision || 0);
        var requestRuntime = root._editorRuntime || (card.dataset && card.dataset.runtime);
        if (requestRuntime) root._editorRuntime = requestRuntime;
        var runtimeHeaders = requestRuntime ? {
            'X-AICF-Runtime': encodeURIComponent(requestRuntime),
            'X-AICF-Runtime-Encoding': 'uri'
        } : {};
        while (root._h3SavePending) {
            await root._h3SavePending;
            if (String(window.AICF.promptRuntimeRevision || 0) !== saveRuntime || String(root.dataset.h3Session || '') !== saveSession) {
                throw new Error('编辑会话已切换，已取消旧保存请求。');
            }
        }
        var finishSave;
        var savePending = new Promise(function(resolve) { finishSave = resolve; });
        root._h3SavePending = savePending;
        if (root._h3AutosaveTimer) {
            window.clearTimeout(root._h3AutosaveTimer);
            root._h3AutosaveTimer = null;
        }
        var originalText = button ? button.textContent : '';
        if (button) { button.disabled = true; button.textContent = '保存中…'; }
        try {
            var manifest = _h3Manifest(root);
            var promptField = root.querySelector('.h3-prompt-override, .aicf-prompt-modal-body');
            var promptSource = !promptField ? root.querySelector('.h3-card-prompt-source') : null;
            var submittedPrompt = promptField
                ? String(promptField.value || '')
                : String(promptSource && promptSource.textContent || '');
            var submittedManifestSnapshot = window.AICF.h3ManifestSignature(manifest);
            var bindingSource = root.querySelector('.h3-asset-list');
            var inputs = root.querySelectorAll('.h3-asset-input');
            for (var index = 0; index < inputs.length; index += 1) {
                var input = inputs[index];
                if (!input.files || !input.files.length) continue;
                var form = new FormData();
                form.append('asset_type', String(input.dataset.mediaType || ''));
                Array.prototype.forEach.call(input.files, function(file) { form.append('files', file); });
                var upload = await _requestJson('/api/segment/' + String(segmentIndex) + '/assets', {method: 'POST', headers:runtimeHeaders, body: form});
                (upload.assets || []).forEach(function(item) { manifest.push(item); });
                input.value = '';
            }
            // The visible list is authoritative. Upload order starts at 1
            // per batch and must not move an appended image ahead of it.
            manifest = manifest.map(function(item, index) {
                return Object.assign({}, item, {order: index + 1});
            });
            var response = await _requestJson('/api/segment/generation-config', {
                method: 'POST',
                headers: Object.assign({'Content-Type': 'application/json'}, runtimeHeaders),
                body: JSON.stringify({
                    segment_index: Number(segmentIndex),
                    generation_mode: 'ref2va',
                    asset_manifest: manifest,
                    base_revision: bindingSource && bindingSource.dataset.baseRevision || undefined,
                    segment_uid: bindingSource && bindingSource.dataset.segmentUid || undefined,
                    prompt_override: submittedPrompt,
                    prompt_provenance: (function() {
                        if (!root.dataset || !root.dataset.promptProvenance || !window.AICF.promptTextMatches(submittedPrompt, root.dataset.promptRevisionPrompt)) return null;
                        try { return JSON.parse(root.dataset.promptProvenance); } catch (error) { return null; }
                    })(),
                    force_save: forceSave === true,
                    refresh_scene_state: refreshState !== false
                })
            });
            if (String(window.AICF.promptRuntimeRevision || 0) !== saveRuntime) return response;
            if(response.state_status&&window.AICF.updateH3StateSummary)window.AICF.updateH3StateSummary(root,response);
            if(response.scene_operation_id&&window.AICF.monitorH3Scene)window.AICF.monitorH3Scene(root,response.scene_operation_id);
            window.AICF.updateSegmentCardState(segmentIndex, response);
            var canonicalConfig = response && response.generation_config && typeof response.generation_config === 'object'
                ? response.generation_config : {};
            var canonicalManifest = Array.isArray(canonicalConfig.asset_manifest) ? canonicalConfig.asset_manifest : manifest;
            var canonicalPrompt = String(response && response.canonical_prompt || submittedPrompt);
            var rootStillMatches = !root.classList.contains('aicf-prompt-modal') || (
                String((root.dataset && root.dataset.segmentIndex) || '') === String(segmentIndex)
                && String((root.dataset && root.dataset.h3Session) || '') === saveSession
            );
            if (!rootStillMatches) return response;
            if (response.editor_snapshot && window.AICF.syncH3EditorBaseline) window.AICF.syncH3EditorBaseline(root, response.editor_snapshot);
            else if (window.AICF.refreshH3EditorBaseline) await window.AICF.refreshH3EditorBaseline(root);
            if (String((root.dataset && root.dataset.h3Session) || '') !== saveSession || String(window.AICF.promptRuntimeRevision || 0) !== saveRuntime) return response;
            if (window.AICF.h3ManifestSignature(_h3Manifest(root)) !== submittedManifestSnapshot ||
                (promptField && String(promptField.value || '') !== submittedPrompt)) {
                if (bindingSource && response.base_revision) bindingSource.dataset.baseRevision = response.base_revision;
                throw new Error('请求时的版本已保存；当前新修改仍保留，请再次保存后继续。');
            }
            var source = root.querySelector('.h3-asset-list');
            if (source) {
                source.dataset.manifest = JSON.stringify(canonicalManifest);
                if (response.base_revision) source.dataset.baseRevision = response.base_revision;
            }
            // Gradio may have recycled/replaced the card during the request.
            card = _segmentCardForIndex(segmentIndex, null);
            var cardSource = card ? card.querySelector('.h3-card-config-source') : null;
            if (cardSource) {
                cardSource.dataset.manifest = JSON.stringify(canonicalManifest);
                if (response.base_revision) cardSource.dataset.baseRevision = response.base_revision;
            }
            var fullscreenTemplate = card ? card.querySelector('.h3-fullscreen-config-template') : null;
            var templateSource = fullscreenTemplate && fullscreenTemplate.content
                ? fullscreenTemplate.content.querySelector('.h3-asset-list')
                : null;
            if (templateSource) templateSource.dataset.manifest = JSON.stringify(canonicalManifest);
            var cardPrompt = card ? card.querySelector('.h3-prompt-override') : null;
            var promptSource = card ? card.querySelector('.prompt-modal-source') : null;
            if (promptSource) promptSource.textContent = canonicalPrompt;
            if (promptField) promptField.value = canonicalPrompt;
            if (cardPrompt && cardPrompt !== promptField) cardPrompt.value = canonicalPrompt;
            if (root.dataset) {
                delete root.dataset.promptRevisionPrompt;
                delete root.dataset.promptProvenance;
            }
            window.AICF.refreshH3AssetCount(root);
            _h3Status(root, forceSave === true ? '已按页面图片顺序和提示词强制保存，素材绑定已更新。' : 'H3 素材、主体定义与参考关系已同步保存。', false);
            if (response.prompt_chars !== undefined) window.AICF.showH3PromptCount(root, response);
            if (root._shotEditor && !root._shotEditor.dirty && !root._shotEditor.reading && !root._shotEditor.saving) {
                await root._shotEditor.read(false);
            }
            if (root.classList.contains('aicf-prompt-modal')) {
                await window.AICF.mountVideoChannels(segmentIndex);
            }
            return response;
        } catch (error) {
            _h3Status(root, error && error.message ? error.message : '保存 H3 配置失败', true);
            throw error;
        } finally {
            if (root._h3SavePending === savePending) root._h3SavePending = null;
            finishSave();
            if (button) { button.disabled = false; button.textContent = originalText || '保存 H3 配置'; }
        }
    };
    window.AICF.regenerateH3Segment = async function(segmentIndex, button, options) {
        options = options || {};
        if (window.AICF.archiveReadOnly) return false;
        var pending = window.AICF._h3VideoRegenPending || (window.AICF._h3VideoRegenPending = {});
        if (pending[String(segmentIndex)]) {
            if (options.throwOnFailure) throw new Error('本段已有视频提交操作正在执行');
            return false;
        }
        pending[String(segmentIndex)] = true;
        var card = _segmentCardForIndex(segmentIndex, button);
        var root = _h3ConfigRoot(segmentIndex, button) || card;
        var originalText = button ? button.textContent : '';
        var regenerationRuntimeRevision = String(window.AICF.promptRuntimeRevision || 0);
        try {
            if (button) { button.disabled = true; button.textContent = '校验中…'; }
            var saveButton = root && root.classList && root.classList.contains('aicf-prompt-modal')
                ? root.querySelector('.aicf-prompt-modal-save')
                : card ? card.querySelector('.h3-prompt-save') : null;
            if (!options.prepared) await window.AICF.saveH3GenerationConfig(segmentIndex, saveButton, root, false, false);
            var channel = await window.AICF.chooseVideoChannel(segmentIndex);
            if (!channel) { if (button) {button.disabled = false; button.textContent = originalText;} if(options.throwOnFailure)throw new Error('尚未选择视频渠道，未提交生成');return false; }
            if (options.guard) options.guard();
            if (channel.uses_workflow) {
            var validation = await _requestJson('/api/segment/validate', {
                method: 'POST',
                headers: options.runtime ? {'Content-Type':'application/json','X-AICF-Runtime':encodeURIComponent(options.runtime),'X-AICF-Runtime-Encoding':'uri'} : {'Content-Type': 'application/json'},
                body: JSON.stringify({segment_index: Number(segmentIndex)})
            });
            if (validation.errors && validation.errors.length) {
                throw new Error(_validationMessage(validation.errors));
            }
            }
            if (options.guard) options.guard();
            if (button) button.textContent = '提交中…';
            if (options.guard) options.guard();
            if (String(window.AICF.promptRuntimeRevision || 0) !== regenerationRuntimeRevision) throw new Error('项目已切换，已取消旧页面视频提交');
            var submission = await _requestJson('/api/segment/regenerate_video', {
                method: 'POST',
                headers: options.runtime ? {'Content-Type':'application/json','X-AICF-Runtime':encodeURIComponent(options.runtime),'X-AICF-Runtime-Encoding':'uri'} : {'Content-Type': 'application/json'},
                body: JSON.stringify({segment_index: Number(segmentIndex), channel_id: channel.id})
            });
            delete pending[String(segmentIndex)];
            var updatedCard = window.AICF.replaceSegmentCardFromResponse(segmentIndex, submission, card, '.primary-mini');
            if (options.prepared && updatedCard && root) root._sourceButton = updatedCard.querySelector('.h3-prompt-open-action') || root._sourceButton;
            if (submission.web_video_task_id) window.AICF.watchWebVideoTask(segmentIndex, submission.web_video_task_id);
            if (options.throwOnFailure) return submission;
        } catch (error) {
            _h3Status(root, error && error.message ? error.message : 'H3 重新生成失败', true);
            if (button) { button.disabled = false; button.textContent = originalText || '验证并重新生成'; }
            if (options.throwOnFailure) throw error;
        } finally {
            delete pending[String(segmentIndex)];
        }
        return false;
    };
    window.__AICF_READY__ = true;
    console.log('[AICF] APP_JS loaded');
}
initAICF();
"""

from ui.browser_runtime_ui import BROWSER_RUNTIME_JS
APP_JS = BROWSER_RUNTIME_JS + APP_JS

from ui.h3_prompt_editor_ui import PROMPT_EDITOR_JS
APP_JS += PROMPT_EDITOR_JS

from ui.web_llm_ui import WEB_LLM_JS

APP_JS += WEB_LLM_JS
from ui.h3_shot_editor_ui import SHOT_EDITOR_JS
APP_JS += SHOT_EDITOR_JS
from ui.text_jobs_ui import PANEL_JS
APP_JS += PANEL_JS

from ui.h3_editor_draft_ui import EDITOR_DRAFT_JS
APP_JS += EDITOR_DRAFT_JS

from ui.h3_composite_ui import COMPOSITE_JS
APP_JS += COMPOSITE_JS

from ui.scene_video_batch_ui import SCENE_BATCH_JS
APP_JS += SCENE_BATCH_JS

# Export only after every editor module has been appended.
APP_JS_FUNCTION = APP_JS
