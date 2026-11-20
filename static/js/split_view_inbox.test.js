const { test, describe, beforeEach } = require('node:test');
const assert = require('node:assert');

// DOM Mock
class MockClassList {
    constructor(element) {
        this.element = element;
        this.classes = new Set();
    }
    add(...names) {
        names.forEach(n => this.classes.add(n));
        this.element._className = Array.from(this.classes).join(' ');
    }
    remove(...names) {
        names.forEach(n => this.classes.delete(n));
        this.element._className = Array.from(this.classes).join(' ');
    }
    contains(name) {
        return this.classes.has(name);
    }
}

class MockElement {
    constructor(tagName) {
        this.tagName = tagName.toUpperCase();
        this._className = '';
        this.classList = new MockClassList(this);
        this.dataset = {};
        this.attributes = {};
        this.children = [];
        this.parentElement = null;
        this._innerHTML = '';
        this._textContent = '';
        this.style = {};
    }

    get firstChild() {
        return this.children[0] || null;
    }

    get innerHTML() {
        if (this._innerHTML) return this._innerHTML;
        return this.children.map(c => c.innerHTML).join('');
    }
    set innerHTML(val) {
        this._innerHTML = val || '';
    }

    get className() {
        return this._className;
    }
    set className(val) {
        this._className = val || '';
        this.classList.classes = new Set(this._className.split(/\s+/).filter(Boolean));
    }

    get textContent() {
        return this._textContent;
    }
    set textContent(val) {
        this._textContent = val;
    }

    setAttribute(k, v) {
        this.attributes[k] = v;
    }
    getAttribute(k) {
        return this.attributes[k] || null;
    }

    appendChild(child) {
        child.parentElement = this;
        this.children.push(child);
        return child;
    }

    prepend(child) {
        return this.insertBefore(child, this.children[0] || null);
    }

    insertBefore(newNode, referenceNode) {
        newNode.parentElement = this;
        const index = this.children.indexOf(referenceNode);
        if (index !== -1) {
            this.children.splice(index, 0, newNode);
        } else {
            this.children.unshift(newNode);
        }
        return newNode;
    }

    closest(selector) {
        let cur = this;
        while (cur) {
            if (cur._matches(selector)) return cur;
            cur = cur.parentElement;
        }
        return null;
    }

    _matches(selector) {
        const sel = selector.trim();
        if (sel.startsWith('.')) {
            return this.classList.contains(sel.slice(1));
        }
        if (sel.startsWith('#')) {
            return this.attributes.id === sel.slice(1);
        }
        return this.tagName.toLowerCase() === sel.toLowerCase();
    }

    remove() {
        if (this.parentElement) {
            const index = this.parentElement.children.indexOf(this);
            if (index !== -1) {
                this.parentElement.children.splice(index, 1);
            }
            this.parentElement = null;
        }
    }

    querySelector(selector) {
        return this.querySelectorAll(selector)[0] || null;
    }

    querySelectorAll(selector) {
        const results = [];
        const sel = selector.trim();

        function match(el) {
            if (sel.startsWith('#')) {
                return el.attributes.id === sel.slice(1);
            }
            if (sel.includes('[data-conversation-id=')) {
                const matchId = sel.match(/\[data-conversation-id="?([^"\]]+)"?\]/);
                if (matchId && el.dataset.conversationId === matchId[1]) return true;
            }
            if (sel.startsWith('.')) {
                const parts = sel.split(/\s+/);
                if (parts.length === 1) {
                    return el.classList.contains(parts[0].slice(1));
                }
            }
            // compound selector like ".inbox-preview-row .badge"
            if (sel === '.inbox-preview-row .badge') {
                return el.classList.contains('badge') && el.parentElement && el.parentElement.classList.contains('inbox-preview-row');
            }
            return false;
        }

        function walk(node) {
            if (match(node)) {
                results.push(node);
            }
            for (const child of node.children) {
                walk(child);
            }
        }

        for (const child of this.children) {
            walk(child);
        }
        return results;
    }
}

global.document = {
    createElement(tag) {
        return new MockElement(tag);
    },
    getElementById(id) {
        return null;
    },
    addEventListener(event, handler) {},
    dispatchEvent(event) {}
};

const SplitViewInbox = require('./split_view_inbox.js');

describe('SplitViewInbox Module', () => {
    let inboxList;
    let sidebarBody;

    function buildItem(convId, name, snippet, time, unreadCount, isActive = false) {
        const li = new MockElement('li');
        li.className = 'inbox-list-item';

        const a = new MockElement('a');
        a.className = `inbox-item d-flex align-items-center gap-3 p-3 text-decoration-none ${isActive ? 'active' : ''} ${unreadCount > 0 ? 'has-unread' : ''}`;
        a.dataset.conversationId = convId;
        a.setAttribute('href', `/messages/${convId}/`);

        const avatarDiv = new MockElement('div');
        avatarDiv.className = 'inbox-avatar-container flex-shrink-0';
        a.appendChild(avatarDiv);

        const content = new MockElement('div');
        content.className = 'inbox-content flex-grow-1 min-w-0';

        const titleRow = new MockElement('div');
        titleRow.className = 'inbox-title-row d-flex justify-content-between align-items-baseline mb-1';
        const nameH6 = new MockElement('h6');
        nameH6.className = 'inbox-counterpart-name mb-0 text-truncate fw-semibold flex-grow-1';
        nameH6.textContent = name;
        const timeSpan = new MockElement('span');
        timeSpan.className = 'inbox-time small text-muted text-nowrap ms-2 flex-shrink-0';
        timeSpan.textContent = time;
        titleRow.appendChild(nameH6);
        titleRow.appendChild(timeSpan);
        content.appendChild(titleRow);

        const previewRow = new MockElement('div');
        previewRow.className = 'inbox-preview-row d-flex justify-content-between align-items-center gap-2';
        const snippetP = new MockElement('p');
        snippetP.className = 'inbox-snippet mb-0 small text-muted text-truncate flex-grow-1';
        snippetP.textContent = snippet;
        previewRow.appendChild(snippetP);

        if (unreadCount > 0) {
            const badge = new MockElement('span');
            badge.className = 'badge rounded-pill bg-primary ms-auto flex-shrink-0';
            badge.textContent = String(unreadCount);
            previewRow.appendChild(badge);
        }
        content.appendChild(previewRow);
        a.appendChild(content);
        li.appendChild(a);
        return li;
    }

    beforeEach(() => {
        sidebarBody = new MockElement('div');
        sidebarBody.className = 'chat-split-sidebar-body';

        inboxList = new MockElement('ul');
        inboxList.className = 'inbox-list list-unstyled m-0 p-0';
        inboxList.setAttribute('id', 'inboxList');
        sidebarBody.appendChild(inboxList);
    });

    test('reorders background conversation to the top and updates snippet and unread badge', () => {
        const item1 = buildItem('c-1', 'Alice', 'Old snippet 1', '1 hour ago', 0);
        const item2 = buildItem('c-2', 'Bob', 'Old snippet 2', '2 hours ago', 0);
        inboxList.appendChild(item1);
        inboxList.appendChild(item2);

        // c-2 sends a message while c-1 is active (or on inbox)
        SplitViewInbox.handleIncomingChatNotification({
            type: 'unread_chat_count',
            count: 1,
            unread_count: 1,
            conversation_id: 'c-2',
            snippet: 'New Bob message',
            sender_name: 'Bob',
            timestamp: new Date().toISOString()
        }, {
            activeConversationId: 'c-1',
            inboxList: inboxList,
            sidebarBody: sidebarBody
        });

        // Item 2 should now be at the top
        assert.strictEqual(inboxList.children[0].querySelector('[data-conversation-id="c-2"]').dataset.conversationId, 'c-2');
        assert.strictEqual(inboxList.children[1].querySelector('[data-conversation-id="c-1"]').dataset.conversationId, 'c-1');

        // Snippet and time updated
        const c2Link = inboxList.children[0].querySelector('[data-conversation-id="c-2"]');
        assert.strictEqual(c2Link.querySelector('.inbox-snippet').textContent, 'New Bob message');
        assert.strictEqual(c2Link.querySelector('.inbox-time').textContent, 'just now');

        // Marked as unread with badge incremented
        assert.strictEqual(c2Link.classList.contains('has-unread'), true);
        const badge = c2Link.querySelector('.inbox-preview-row .badge');
        assert.notStrictEqual(badge, null);
        assert.strictEqual(badge.textContent, '1');
    });

    test('active conversation updates snippet and moves to top without showing unread badge', () => {
        const item1 = buildItem('c-1', 'Alice', 'Old snippet 1', '1 hour ago', 0, true);
        const item2 = buildItem('c-2', 'Bob', 'Old snippet 2', '2 hours ago', 0);
        inboxList.appendChild(item2);
        inboxList.appendChild(item1);

        // c-1 is active and receives message
        SplitViewInbox.handleIncomingChatNotification({
            type: 'unread_chat_count',
            count: 1,
            unread_count: 1,
            conversation_id: 'c-1',
            snippet: 'New Alice message',
            sender_name: 'Alice',
            timestamp: new Date().toISOString()
        }, {
            activeConversationId: 'c-1',
            inboxList: inboxList,
            sidebarBody: sidebarBody
        });

        // c-1 should be at top
        assert.strictEqual(inboxList.children[0].querySelector('[data-conversation-id="c-1"]').dataset.conversationId, 'c-1');

        // Snippet updated
        const c1Link = inboxList.children[0].querySelector('[data-conversation-id="c-1"]');
        assert.strictEqual(c1Link.querySelector('.inbox-snippet').textContent, 'New Alice message');

        // No unread class or badge for active conversation
        assert.strictEqual(c1Link.classList.contains('has-unread'), false);
        const badge = c1Link.querySelector('.inbox-preview-row .badge');
        assert.strictEqual(badge, null);
    });

    test('Safeguard 1: XSS payload in snippet or sender name is set via textContent without innerHTML execution', () => {
        const item1 = buildItem('c-1', 'Alice', 'Old snippet 1', '1 hour ago', 0);
        inboxList.appendChild(item1);

        const xssSnippet = '<img src=x onerror=alert(1)>';
        const xssName = '<script>alert("hack")</script>';

        SplitViewInbox.handleIncomingChatNotification({
            type: 'unread_chat_count',
            count: 1,
            unread_count: 1,
            conversation_id: 'c-1',
            snippet: xssSnippet,
            sender_name: xssName,
            timestamp: new Date().toISOString()
        }, {
            activeConversationId: null,
            inboxList: inboxList,
            sidebarBody: sidebarBody
        });

        const link = inboxList.children[0].querySelector('[data-conversation-id="c-1"]');
        assert.strictEqual(link.querySelector('.inbox-snippet').textContent, xssSnippet);
    });

    test('Safeguard 2: newly arriving conversation prepends clean row matching _conversation_list markup', () => {
        // Starts with empty inbox list
        assert.strictEqual(inboxList.children.length, 0);

        SplitViewInbox.handleIncomingChatNotification({
            type: 'unread_chat_count',
            count: 1,
            unread_count: 1,
            conversation_id: 'c-new',
            conversation_url: '/messages/c-new/',
            snippet: 'First inquiry!',
            sender_name: 'Charlie Brown',
            sender_initial: 'C',
            sender_color: '#10b981',
            timestamp: new Date().toISOString()
        }, {
            activeConversationId: null,
            inboxList: inboxList,
            sidebarBody: sidebarBody
        });

        assert.strictEqual(inboxList.children.length, 1);
        const link = inboxList.children[0].querySelector('[data-conversation-id="c-new"]');
        assert.notStrictEqual(link, null);
        assert.strictEqual(link.getAttribute('href'), '/messages/c-new/');
        assert.strictEqual(link.querySelector('.inbox-counterpart-name').textContent, 'Charlie Brown');
        assert.strictEqual(link.querySelector('.inbox-snippet').textContent, 'First inquiry!');
        assert.strictEqual(link.querySelector('.inbox-preview-row .badge').textContent, '1');
    });

    test('Safeguard 3: navbar badge syncs when message arrives for active conversation', () => {
        const item1 = buildItem('c-1', 'Alice', 'Old snippet 1', '1 hour ago', 0, true);
        inboxList.appendChild(item1);

        let updatedNavbarCount = null;
        global.window = {
            NotificationController: {
                updateUnreadChatCount: (c) => {
                    updatedNavbarCount = c;
                }
            }
        };

        SplitViewInbox.handleIncomingChatNotification({
            type: 'unread_chat_count',
            count: 1,
            unread_count: 1,
            conversation_id: 'c-1',
            snippet: 'Message for open thread',
            sender_name: 'Alice',
            timestamp: new Date().toISOString()
        }, {
            activeConversationId: 'c-1',
            inboxList: inboxList,
            sidebarBody: sidebarBody,
            navbarSync: true
        });

        assert.strictEqual(updatedNavbarCount, 0);
    });
});
