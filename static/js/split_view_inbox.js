(function (root, factory) {
    if (typeof define === 'function' && define.amd) {
        define([], factory);
    } else if (typeof module === 'object' && module.exports) {
        module.exports = factory();
    } else {
        root.SplitViewInbox = factory();
    }
}(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    function handleIncomingChatNotification(data, options = {}) {
        if (!data || !data.conversation_id) {
            return null;
        }

        const activeId = options.activeConversationId ? String(options.activeConversationId) : null;
        const convId = String(data.conversation_id);
        const isActive = Boolean(activeId && activeId === convId);

        let sidebarBody = options.sidebarBody;
        if (!sidebarBody && typeof document !== 'undefined') {
            sidebarBody = document.querySelector('.chat-split-sidebar-body');
        }

        let inboxList = options.inboxList;
        if (!inboxList && typeof document !== 'undefined') {
            inboxList = document.getElementById('inboxList');
        }

        // If inboxList not found, check if there is an empty state container in sidebar
        if (!inboxList && sidebarBody) {
            const emptyState = sidebarBody.querySelector('.inbox-empty-state');
            if (emptyState) {
                emptyState.remove();
            }
            if (typeof document !== 'undefined') {
                inboxList = document.createElement('ul');
                inboxList.className = 'inbox-list list-unstyled m-0 p-0';
                inboxList.setAttribute('id', 'inboxList');
                sidebarBody.appendChild(inboxList);
            }
        }

        if (!inboxList) {
            return null;
        }

        // Find existing conversation item
        const existingLink = inboxList.querySelector(`[data-conversation-id="${convId}"]`);

        if (existingLink) {
            // Update existing conversation item
            const li = existingLink.closest ? existingLink.closest('.inbox-list-item') : existingLink.parentElement;

            const snippetEl = existingLink.querySelector('.inbox-snippet');
            if (snippetEl) {
                snippetEl.textContent = data.snippet || 'No message text';
            }

            const timeEl = existingLink.querySelector('.inbox-time');
            if (timeEl) {
                timeEl.textContent = 'just now';
                if (data.timestamp) {
                    try {
                        timeEl.setAttribute('title', new Date(data.timestamp).toLocaleString());
                    } catch (e) {}
                }
            }

            if (isActive) {
                existingLink.classList.remove('has-unread');
                existingLink.classList.add('active');
                const badge = existingLink.querySelector('.inbox-preview-row .badge');
                if (badge) {
                    badge.remove();
                }

                // Navbar unread badge sync for active conversations
                if (options.navbarSync !== false) {
                    syncActiveNavbarBadge(data);
                }
            } else {
                existingLink.classList.add('has-unread');
                let badge = existingLink.querySelector('.inbox-preview-row .badge');
                const nextVal = typeof data.conversation_unread_count !== 'undefined'
                    ? data.conversation_unread_count
                    : ((parseInt(badge ? badge.textContent : 0, 10) || 0) + 1);
                if (badge) {
                    badge.textContent = String(nextVal);
                    badge.setAttribute('aria-label', `${nextVal} unread messages`);
                } else {
                    const previewRow = existingLink.querySelector('.inbox-preview-row');
                    if (previewRow && typeof document !== 'undefined') {
                        badge = document.createElement('span');
                        badge.className = 'badge rounded-pill bg-primary ms-auto flex-shrink-0';
                        badge.textContent = String(nextVal);
                        badge.setAttribute('aria-label', `${nextVal} unread messages`);
                        previewRow.appendChild(badge);
                    }
                }
            }

            // Move row to top of the list
            if (li && inboxList.firstChild !== li) {
                if (typeof inboxList.prepend === 'function') {
                    inboxList.prepend(li);
                } else {
                    inboxList.insertBefore(li, inboxList.firstChild);
                }
            }

            return li;
        }

        // Row does not exist yet: create brand-new row with XSS-safe DOM methods
        if (typeof document === 'undefined') {
            return null;
        }

        // Remove empty state if present
        if (sidebarBody) {
            const emptyState = sidebarBody.querySelector('.inbox-empty-state');
            if (emptyState) {
                emptyState.remove();
            }
        }

        const li = document.createElement('li');
        li.className = 'inbox-list-item';

        const a = document.createElement('a');
        a.className = 'inbox-item d-flex align-items-center gap-3 p-3 text-decoration-none';
        if (isActive) {
            a.classList.add('active');
        } else {
            a.classList.add('has-unread');
        }
        a.dataset.conversationId = convId;
        const targetUrl = data.conversation_url || (`/messages/${encodeURIComponent(convId)}/`);
        a.setAttribute('href', targetUrl);

        // Avatar container
        const avatarContainer = document.createElement('div');
        avatarContainer.className = 'inbox-avatar-container flex-shrink-0';
        if (data.sender_avatar_url) {
            const img = document.createElement('img');
            img.src = data.sender_avatar_url;
            img.alt = `${data.sender_name || 'User'}'s avatar`;
            img.className = 'rounded-circle';
            img.style.width = '48px';
            img.style.height = '48px';
            img.style.objectFit = 'cover';
            avatarContainer.appendChild(img);
        } else {
            const fallbackDiv = document.createElement('div');
            fallbackDiv.className = 'rounded-circle d-flex align-items-center justify-content-center text-white';
            fallbackDiv.style.width = '48px';
            fallbackDiv.style.height = '48px';
            fallbackDiv.style.backgroundColor = data.sender_color || '#1e3a8a';
            fallbackDiv.style.fontWeight = '600';
            fallbackDiv.setAttribute('role', 'img');
            fallbackDiv.setAttribute('aria-label', `${data.sender_name || 'User'}'s avatar`);
            fallbackDiv.textContent = data.sender_initial || (data.sender_name ? data.sender_name[0].toUpperCase() : '?');
            avatarContainer.appendChild(fallbackDiv);
        }
        a.appendChild(avatarContainer);

        // Content container
        const content = document.createElement('div');
        content.className = 'inbox-content flex-grow-1 min-w-0';
        content.style.minWidth = '0';
        content.style.overflow = 'hidden';

        // Title row
        const titleRow = document.createElement('div');
        titleRow.className = 'inbox-title-row d-flex justify-content-between align-items-baseline mb-1';
        titleRow.style.minWidth = '0';

        const nameH6 = document.createElement('h6');
        nameH6.className = 'inbox-counterpart-name mb-0 text-truncate fw-semibold flex-grow-1';
        nameH6.style.minWidth = '0';
        nameH6.textContent = data.sender_name || 'Student'; // Safe textContent
        titleRow.appendChild(nameH6);

        const timeSpan = document.createElement('span');
        timeSpan.className = 'inbox-time small text-muted text-nowrap ms-2 flex-shrink-0';
        timeSpan.textContent = 'just now';
        if (data.timestamp) {
            try {
                timeSpan.setAttribute('title', new Date(data.timestamp).toLocaleString());
            } catch (e) {}
        }
        titleRow.appendChild(timeSpan);
        content.appendChild(titleRow);

        // Preview row
        const previewRow = document.createElement('div');
        previewRow.className = 'inbox-preview-row d-flex justify-content-between align-items-center gap-2';
        previewRow.style.minWidth = '0';

        const snippetP = document.createElement('p');
        snippetP.className = 'inbox-snippet mb-0 small text-muted text-truncate flex-grow-1';
        snippetP.style.minWidth = '0';
        snippetP.textContent = data.snippet || 'No message text'; // Safe textContent
        previewRow.appendChild(snippetP);

        if (!isActive) {
            const badge = document.createElement('span');
            badge.className = 'badge rounded-pill bg-primary ms-auto flex-shrink-0';
            const countVal = typeof data.conversation_unread_count !== 'undefined'
                ? data.conversation_unread_count
                : 1;
            badge.textContent = String(countVal);
            badge.setAttribute('aria-label', `${countVal} unread messages`);
            previewRow.appendChild(badge);
        }
        content.appendChild(previewRow);

        a.appendChild(content);
        li.appendChild(a);

        if (typeof inboxList.prepend === 'function') {
            inboxList.prepend(li);
        } else {
            inboxList.insertBefore(li, inboxList.firstChild);
        }

        if (isActive && options.navbarSync !== false) {
            syncActiveNavbarBadge(data);
        }

        return li;
    }

    function syncActiveNavbarBadge(data) {
        if (typeof window !== 'undefined' && window.NotificationController && typeof window.NotificationController.updateUnreadChatCount === 'function') {
            const rawCount = typeof data.count !== 'undefined' ? data.count : (data.unread_count || 0);
            const corrected = Math.max(0, (parseInt(rawCount, 10) || 0) - 1);
            window.NotificationController.updateUnreadChatCount(corrected);
        }
    }

    function initSplitViewInbox(options = {}) {
        if (typeof document === 'undefined') return;

        const container = options.container || document.getElementById('chatContainer');
        const activeConversationId = options.activeConversationId || (container ? container.dataset.conversationId : null);

        document.addEventListener('tutor:chat:unread_count', function (event) {
            if (!event || !event.detail) return;
            handleIncomingChatNotification(event.detail, {
                activeConversationId: activeConversationId,
                inboxList: options.inboxList || document.getElementById('inboxList'),
                sidebarBody: options.sidebarBody || document.querySelector('.chat-split-sidebar-body'),
                navbarSync: true
            });
        });
    }

    return {
        handleIncomingChatNotification,
        syncActiveNavbarBadge,
        initSplitViewInbox
    };
}));
