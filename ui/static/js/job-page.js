/**
 * Single job progress page
 */
document.addEventListener('DOMContentLoaded', function() {
    const cfg = window.JOB_PAGE_CONFIG || {};
    const jobId = cfg.jobId;
    const jobUrl = cfg.jobUrl;
    const socket = io();

    const jobsList = document.getElementById('jobs-list');
    const template = document.getElementById('job-card-template');
    if (!template || !jobId) return;

    const clone = template.content.cloneNode(true);
    const card = clone.querySelector('.job-card');
    card.dataset.jobId = jobId;
    card.querySelector('.job-url').textContent = jobUrl;
    card.querySelector('.cancel-job-btn').addEventListener('click', () => cancelJob(jobId));
    jobsList.appendChild(clone);

    const cardEl = document.querySelector('.job-card[data-job-id="' + jobId + '"]');

    socket.on('connect', () => {
        socket.emit('subscribe_job', { job_id: jobId });
        refreshJob();
    });

    socket.on('job_progress', (data) => {
        if (data.job_id === jobId) updateUI(data);
    });

    socket.on('job_complete', (data) => {
        if (data.job_id !== jobId) return;
        updateUI({ stage: 'complete', message: 'Completed!', percent: 100 });
        if (window.PickARecipeNotifications) {
            PickARecipeNotifications.onJobComplete(data.recipe);
        }
    });

    socket.on('job_failed', (data) => {
        if (data.job_id !== jobId) return;
        updateUI({ stage: 'error', message: data.error, percent: 0 });
        if (window.PickARecipeNotifications) {
            PickARecipeNotifications.onJobFailed(data.error);
        }
    });

    async function refreshJob() {
        const resp = await fetch('/api/jobs/' + jobId);
        if (!resp.ok) return;
        const job = await resp.json();
        updateUI({
            stage: job.current_stage || job.status,
            message: job.stage_message || '',
            percent: job.progress || 0,
            video_title: job.video_title,
            queue_position: job.queue_position,
        });
    }

    function updateUI(data) {
        if (!cardEl) return;
        if (data.video_title) {
            cardEl.querySelector('.job-title').textContent = data.video_title;
        }
        let msg = data.message || '';
        if (data.queue_position > 0) {
            msg = 'Queued — position ' + data.queue_position;
        }
        cardEl.querySelector('.message-text').textContent = msg;
        cardEl.querySelector('.progress-bar').style.width = (data.percent || 0) + '%';
        cardEl.querySelector('.progress-text').textContent = (data.percent || 0) + '%';
    }

    async function cancelJob(id) {
        await fetch('/api/jobs/' + id, { method: 'DELETE' });
    }

    if (window.PickARecipeNotifications) {
        PickARecipeNotifications.requestPermission();
    }
});
