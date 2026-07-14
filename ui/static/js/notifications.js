/**
 * Browser notifications and push subscription helpers.
 */
window.PickARecipeNotifications = {
    async requestPermission() {
        if (!('Notification' in window)) return false;
        if (Notification.permission === 'granted') return true;
        if (Notification.permission === 'denied') return false;
        const result = await Notification.requestPermission();
        return result === 'granted';
    },

    notify(title, options = {}) {
        if (!('Notification' in window) || Notification.permission !== 'granted') return;
        try {
            const n = new Notification(title, {
                icon: '/static/icons/icon-192x192.png',
                badge: '/static/icons/icon-128x128.png',
                ...options,
            });
            n.onclick = () => {
                window.focus();
                n.close();
            };
        } catch (e) {
            console.warn('Notification failed', e);
        }
    },

    async subscribePush() {
        if (!('serviceWorker' in navigator) || !('PushManager' in window)) return false;
        try {
            const reg = await navigator.serviceWorker.ready;
            const sub = await reg.pushManager.subscribe({
                userVisibleOnly: true,
                applicationServerKey: null,
            }).catch(() => null);
            if (!sub) return false;
            await fetch('/api/push/subscribe', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ subscription: sub.toJSON() }),
            });
            return true;
        } catch (e) {
            console.warn('Push subscribe failed', e);
            return false;
        }
    },

    onJobComplete(recipe) {
        this.notify('Recipe ready!', {
            body: recipe?.name || 'Extraction completed successfully',
            tag: 'job-complete',
        });
    },

    onJobFailed(error) {
        this.notify('Extraction failed', {
            body: error || 'Unknown error',
            tag: 'job-failed',
        });
    },
};
