let currentFormat = 'smart';
let pollInterval = null;
let currentTab = 'single';
let previewDataCache = null;

function switchTab(tab) {
    currentTab = tab;
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelector(`button[onclick="switchTab('${tab}')"]`).classList.add('active');

    if (tab === 'single') {
        document.getElementById('singleInput').classList.remove('hidden');
        document.getElementById('batchInput').classList.add('hidden');
    } else {
        document.getElementById('singleInput').classList.add('hidden');
        document.getElementById('batchInput').classList.remove('hidden');
    }
}

function selectFormat(element, format) {
    document.querySelectorAll('.config-card').forEach(c => c.classList.remove('selected'));
    element.classList.add('selected');
    currentFormat = format;
}

function toggleAdvanced() {
    const p = document.getElementById('advancedSettings');
    const i = document.getElementById('advIcon');
    if (p.classList.contains('hidden')) {
        p.classList.remove('hidden');
        i.classList.remove('fa-chevron-down');
        i.classList.add('fa-chevron-up');
    } else {
        p.classList.add('hidden');
        i.classList.remove('fa-chevron-up');
        i.classList.add('fa-chevron-down');
    }
}

function getUrlInput() {
    if (currentTab === 'single') {
        return document.getElementById('deckUrl').value.trim();
    } else {
        return document.getElementById('batchUrls').value.trim();
    }
}

// Update cut line preview styles dynamically
document.getElementById('cutColor').addEventListener('input', updateCutLinePreview);
document.getElementById('thicknessSlider').addEventListener('input', updateCutLinePreview);

function updateCutLinePreview() {
    const color = document.getElementById('cutColor').value;
    const thickness = document.getElementById('thicknessSlider').value; // mm
    // Convert mm to px approx (2.5 scale)
    const thicknessPx = Math.max(0.5, thickness * 2.5);

    document.querySelectorAll('.cut-line').forEach(line => {
        line.style.backgroundColor = color;
        if (line.classList.contains('vertical')) {
            line.style.width = `${thicknessPx}px`;
        } else {
            line.style.height = `${thicknessPx}px`;
        }
    });
}

function updateLoading(text, percent) {
    document.getElementById('loadingText').innerText = text;
    document.getElementById('loadingFill').style.width = `${percent}%`;
}

async function startPreview() {
    const url = getUrlInput();
    if (!url) {
        alert("Please enter at least one URL.");
        return;
    }

    const previewBtn = document.getElementById('previewBtn');
    if (previewBtn) {
        previewBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i>'; // condensed loader for inline
        previewBtn.disabled = true;
    }
    const previewBtnBatch = document.getElementById('previewBtnBatch');
    if (previewBtnBatch) {
        previewBtnBatch.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Loading...';
        previewBtnBatch.disabled = true;
    }

    try {
        const response = await fetch('/api/preview', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                url: url,
                format: currentFormat,
                include_sideboard: document.getElementById('includeSideboard').checked,
                include_maybeboard: document.getElementById('includeMaybeboard').checked
            })
        });

        if (!response.ok) throw new Error("Failed to fetch preview");

        const data = await response.json();
        renderPreview(data.decks);

        document.getElementById('previewSection').classList.remove('hidden');
        document.getElementById('previewSection').scrollIntoView({ behavior: 'smooth' });

    } catch (e) {
        alert(e.message);
    } finally {
        if (previewBtn) {
            previewBtn.innerHTML = '<i class="fa-solid fa-eye"></i>';
            previewBtn.disabled = false;
        }
        if (previewBtnBatch) {
            previewBtnBatch.innerHTML = '<i class="fa-solid fa-eye"></i> PREVIEW BATCH';
            previewBtnBatch.disabled = false;
        }
    }
}

function renderPreview(decks) {
    const container = document.getElementById('previewContainer');
    container.innerHTML = '';

    // Constants for layout (2.5 scale)
    const CARD_W = 157.5;
    const CARD_H = 220;
    const PAGE_W = 540;
    const PAGE_H = 699;
    const GRID_COLS = 3;
    const GRID_ROWS = 3;
    // START_X/Y for drawing lines centered on the page.
    const START_X = (PAGE_W - (GRID_COLS * CARD_W)) / 2;
    const START_Y = (PAGE_H - (GRID_ROWS * CARD_H)) / 2;

    decks.forEach(deck => {
        const deckDiv = document.createElement('div');
        deckDiv.className = 'preview-deck';
        deckDiv.innerHTML = `<h4>${deck.name} <span style="font-size:0.8em; color:#94a3b8">by ${deck.author}</span></h4>`;

        deck.batches.forEach(batch => {
            const batchLabel = document.createElement('div');
            batchLabel.className = 'preview-batch-label';
            batchLabel.innerText = batch.label;
            deckDiv.appendChild(batchLabel);

            const gridWrapper = document.createElement('div');
            gridWrapper.className = 'preview-grid-wrapper';

            batch.pages.forEach((page, idx) => {
                const pageDiv = document.createElement('div');
                pageDiv.className = 'preview-page';

                // Grid for Cards
                const gridDiv = document.createElement('div');
                gridDiv.className = 'preview-page-grid';
                gridDiv.style.left = `${START_X}px`;
                gridDiv.style.top = `${START_Y}px`;

                page.cards.forEach(imgUrl => {
                    const slot = document.createElement('div');
                    slot.className = 'preview-card-slot';
                    if (imgUrl) {
                        const img = document.createElement('img');
                        img.src = imgUrl; // 'normal' quality now
                        img.className = 'preview-card-img';
                        slot.appendChild(img);
                    }
                    gridDiv.appendChild(slot);
                });

                pageDiv.appendChild(gridDiv);

                // Cut Lines Overlay
                const overlay = document.createElement('div');
                overlay.className = 'cut-lines-overlay';

                // Content will be filled by updatePreviewLayout() relative to padding

                pageDiv.appendChild(overlay);

                // Footer
                const footer = document.createElement('div');
                footer.className = 'preview-footer';
                const footerLeft = document.createElement('span');
                footerLeft.className = 'footer-left';
                const footerText = `${deck.name} - ${deck.author}${page.type === 'back' ? ' (Backs)' : ''}`;
                footerLeft.innerText = footerText;

                const footerRight = document.createElement('span');
                footerRight.className = 'footer-right';
                footerRight.innerText = `${idx + 1} / ${batch.pages.length}`;

                footer.appendChild(footerLeft);
                footer.appendChild(footerRight);
                pageDiv.appendChild(footer);

                pageDiv.innerHTML += `<div class="page-label">Page ${idx + 1} (${page.type})</div>`;
                gridWrapper.appendChild(pageDiv);
            });

            deckDiv.appendChild(gridWrapper);
        });

        container.appendChild(deckDiv);
    });

    // Apply current cut line settings
    updatePreviewLayout();
    updateCutLinePreview(); // Updates colors/thickness
}

// Add padding listener
document.getElementById('paddingSlider').addEventListener('input', () => {
    document.getElementById('paddingVal').innerText = document.getElementById('paddingSlider').value;
    updatePreviewLayout();
});

function updatePreviewLayout() {
    const paddingMm = parseFloat(document.getElementById('paddingSlider').value) || 0;
    const paddingPx = paddingMm * 2.5;

    const CARD_W = 157.5;
    const CARD_H = 220;
    const PAGE_W = 540;
    const PAGE_H = 699;
    const GRID_COLS = 3;
    const GRID_ROWS = 3;
    const FOOTER_BELOW_MM = 0.2;
    const FOOTER_OFFSET = FOOTER_BELOW_MM * 2.5;

    // Grid Dimensions with Gap
    const totalGridW = (GRID_COLS * CARD_W) + ((GRID_COLS - 1) * paddingPx);
    const totalGridH = (GRID_ROWS * CARD_H) + ((GRID_ROWS - 1) * paddingPx);

    const startX = (PAGE_W - totalGridW) / 2;
    const startY = (PAGE_H - totalGridH) / 2;

    // Update all pages
    document.querySelectorAll('.preview-page').forEach(page => {
        // Update Grid Gap and Position
        const grid = page.querySelector('.preview-page-grid');
        if (grid) {
            grid.style.columnGap = `${paddingPx}px`;
            grid.style.rowGap = `${paddingPx}px`;
            grid.style.left = `${startX}px`;
            grid.style.top = `${startY}px`;
        }

        // Update Footer Position
        const footer = page.querySelector('.preview-footer');
        if (footer) {
            footer.style.left = `${startX}px`;
            footer.style.width = `${totalGridW}px`;
            footer.style.top = `${startY + totalGridH + FOOTER_OFFSET}px`;
        }

        // Redraw Cut Lines
        const overlay = page.querySelector('.cut-lines-overlay');
        if (overlay) {
            overlay.innerHTML = ''; // Clear existing

            // Vertical Lines
            for (let c = 0; c < GRID_COLS; c++) {
                // Card Left edge
                const cardLeft = startX + c * (CARD_W + paddingPx);
                const cardRight = cardLeft + CARD_W;

                // Add lines. Note: If padding > 0, we see separation.
                // PDF generator draws lines at card edges extending to infinity (page edge).

                // Left Line of Card C
                createLine(overlay, cardLeft, 0, 1, '100%', 'vertical');
                // Right Line of Card C
                createLine(overlay, cardRight, 0, 1, '100%', 'vertical');
            }

            // Horizontal Lines
            for (let r = 0; r < GRID_ROWS; r++) {
                const cardTop = startY + r * (CARD_H + paddingPx);
                const cardBottom = cardTop + CARD_H;

                createLine(overlay, 0, cardTop, '100%', 1, 'horizontal');
                createLine(overlay, 0, cardBottom, '100%', 1, 'horizontal');
            }
        }
    });

    // Re-apply style settings to new lines
    updateCutLinePreview();
}

function createLine(parent, x, y, w, h, type) {
    const line = document.createElement('div');
    line.className = `cut-line ${type}`;
    line.style.left = x === 0 ? '0' : `${x}px`;
    line.style.top = y === 0 ? '0' : `${y}px`;
    line.style.width = typeof w === 'number' ? `${w}px` : w;
    line.style.height = typeof h === 'number' ? `${h}px` : h;
    parent.appendChild(line);
}


async function startForge() {
    const url = getUrlInput();

    // UI Transition
    document.getElementById('previewSection').classList.add('hidden');
    document.getElementById('loadingSection').classList.remove('hidden');
    updateLoading("Initializing Forge...", 0);

    // Clear download
    document.getElementById('downloadSection').classList.add('hidden');

    try {
        const response = await fetch('/api/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                url: url,
                format: currentFormat,
                padding: parseFloat(document.getElementById('paddingSlider').value),
                include_sideboard: document.getElementById('includeSideboard').checked,
                include_maybeboard: document.getElementById('includeMaybeboard').checked,
                cut_line_color: document.getElementById('cutColor').value,
                cut_line_thickness: parseFloat(document.getElementById('thicknessSlider').value)
            })
        });

        if (!response.ok) throw new Error("Job failed to start");
        const data = await response.json();

        pollInterval = setInterval(() => checkStatus(data.job_id), 1000);

    } catch (e) {
        alert(e.message);
        resetForge();
    }
}

async function checkStatus(jobId) {
    try {
        const response = await fetch(`/api/status/${jobId}`);
        const data = await response.json();

        // Update Loading Bar Text with latest message
        if (data.messages && data.messages.length > 0) {
            updateLoading(data.messages[data.messages.length - 1], data.progress);
        } else {
            updateLoading("Processing...", data.progress);
        }

        if (data.status === 'completed') {
            clearInterval(pollInterval);
            finishJob(data.files);
        } else if (data.status === 'failed') {
            clearInterval(pollInterval);
            alert("Forge Failed: " + data.messages.join('\n'));
            resetForge();
        }
    } catch (e) {
        console.error(e);
    }
}

function finishJob(files) {
    document.getElementById('loadingSection').classList.add('hidden');
    document.getElementById('downloadSection').classList.remove('hidden');

    const container = document.getElementById('downloadLinks');
    container.innerHTML = '';

    files.forEach(filename => {
        const a = document.createElement('a');
        a.href = `/api/download/${filename}`;
        a.className = 'download-btn';
        a.innerHTML = `<i class="fa-solid fa-file-pdf"></i> Download ${filename}`;
        a.target = '_blank';
        container.appendChild(a);
    });
}

function resetForge() {
    document.getElementById('loadingSection').classList.add('hidden');
    document.getElementById('downloadSection').classList.add('hidden');
    // Don't show preview again, just let user edit inputs
}
