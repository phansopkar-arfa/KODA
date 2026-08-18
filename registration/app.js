const presetData = {
    personality: ['Curious', 'Shy', 'Energetic', 'Creative', 'Calm', 'Playful', 'Sensitive', 'Adventurous', 'Thoughtful', 'Funny'],
    interests: ['Dinosaurs', 'Space', 'Animals', 'Art/Drawing', 'Music', 'Cars/Vehicles', 'Sports', 'Cooking', 'Nature', 'Superheroes', 'Science', 'Reading', 'Gaming'],
    neuro: ['ADHD', 'Autism Spectrum', 'Dyslexia', 'Speech Delay', 'Sensory Processing', 'None'],
    pronunciation: ['R sounds', 'L-blends', 'S sounds', 'TH sounds', 'CH/SH sounds', 'Stuttering/Fluency', 'F/V sounds']
};

let currentPage = 1;
let formData = {
    personality: [],
    interests: [],
    neuro: [],
    pronunciation: []
};
let voiceRecording = null;
let recordingInterval = null;
let audioContext = null;
let mediaStream = null;
let processor = null;
let source = null;
let pcmData = [];

document.addEventListener('DOMContentLoaded', () => {
    initChips();
    setupEventListeners();
    loadExistingProfile();
});

let hasExistingVoice = false;

function loadExistingProfile() {
    fetch('/api/profile')
        .then(res => {
            if (res.ok) return res.json();
            throw new Error('No profile');
        })
        .then(profile => {
            if (!profile.exists) return;
            
            // populate personal
            document.getElementById('child-name').value = profile.personal.name || '';
            document.getElementById('child-dob').value = profile.personal.date_of_birth || '';
            updateAgeDisplay();
            
            const p = profile.personal.gender_pronouns || 'they/them';
            const select = document.getElementById('child-pronouns');
            const custom = document.getElementById('custom-pronouns');
            if (['he/him', 'she/her', 'they/them'].includes(p)) {
                select.value = p;
            } else {
                select.value = 'custom';
                custom.classList.remove('hidden');
                custom.value = p;
            }
            
            // populate textareas
            document.getElementById('sibling-info').value = profile.personality.sibling_info || '';
            document.getElementById('neuro-context').value = profile.personality.neurodiversity || '';
            document.getElementById('speech-goals').value = profile.personality.speech_goals || '';
            document.getElementById('additional-notes').value = profile.personality.additional_notes || '';
            document.getElementById('allergies').value = profile.health_routine.allergies_medical || '';
            document.getElementById('routines').value = profile.health_routine.daily_routines || '';
            
            // select chips
            selectChips('personality', profile.personality.traits);
            selectChips('interests', profile.personality.likes_interests);
            selectChips('pronunciation', profile.personality.pronunciation_focus);
            
            // update UI state
            const btnSubmit = document.getElementById('btn-submit');
            btnSubmit.innerText = 'Update Profile & Start KODA';
            
            if (profile.has_voice) {
                hasExistingVoice = true;
                btnSubmit.disabled = false;
                const btnRecord = document.getElementById('btn-record');
                btnRecord.innerText = 'Hold to Re-record Voice';
            }
        })
        .catch(err => console.log('No existing profile to load'));
}

function selectChips(category, values) {
    if (!values || !Array.isArray(values)) return;
    values.forEach(val => {
        let chip = document.querySelector(`.chip[data-value="${val}"]`);
        if (!chip) {
            const addBtn = document.querySelector(`#chips-${category} .chip-add`);
            if (addBtn) {
                chip = createChip(val, category, true);
                addBtn.parentNode.insertBefore(chip, addBtn);
            }
        }
        if (chip && !chip.classList.contains('selected')) {
            chip.classList.add('selected');
            formData[category].push(val);
        }
    });
}

function initChips() {
    Object.keys(presetData).forEach(category => {
        const container = document.getElementById(`chips-${category}`);
        if (!container) return;
        
        presetData[category].forEach(preset => {
            const chip = createChip(preset, category);
            container.appendChild(chip);
        });

        const addChipBtn = document.createElement('div');
        addChipBtn.className = 'chip chip-add';
        addChipBtn.innerHTML = '+ Add';
        addChipBtn.addEventListener('click', () => showAddChipInput(addChipBtn, category));
        container.appendChild(addChipBtn);
    });
}

function createChip(text, category, isCustom = false) {
    const chip = document.createElement('div');
    chip.className = 'chip';
    chip.dataset.value = text;
    chip.innerText = text;
    
    if (isCustom) {
        const removeBtn = document.createElement('span');
        removeBtn.className = 'chip-remove';
        removeBtn.innerHTML = '×';
        removeBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            toggleChipState(chip, category, false);
            chip.remove();
        });
        chip.appendChild(removeBtn);
    }

    chip.addEventListener('click', () => {
        const isSelected = chip.classList.toggle('selected');
        toggleChipState(chip, category, isSelected);
    });
    
    return chip;
}

function toggleChipState(chip, category, isSelected) {
    const val = chip.dataset.value;
    if (isSelected) {
        if (!formData[category].includes(val)) formData[category].push(val);
    } else {
        formData[category] = formData[category].filter(item => item !== val);
    }
}

function showAddChipInput(addBtn, category) {
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'custom-chip-input';
    input.placeholder = 'Type...';
    
    addBtn.innerHTML = '';
    addBtn.appendChild(input);
    input.focus();

    const finish = () => {
        const val = input.value.trim();
        if (val) {
            const newChip = createChip(val, category, true);
            newChip.classList.add('selected');
            formData[category].push(val);
            addBtn.parentNode.insertBefore(newChip, addBtn);
        }
        addBtn.innerHTML = '+ Add';
    };

    input.addEventListener('blur', finish);
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') finish();
    });
}

function setupEventListeners() {
    document.getElementById('child-dob').addEventListener('input', updateAgeDisplay);
    document.getElementById('child-pronouns').addEventListener('change', (e) => {
        document.getElementById('custom-pronouns').classList.toggle('hidden', e.target.value === 'custom' ? false : true);
    });

    document.getElementById('btn-next').addEventListener('click', () => navigateTo(currentPage + 1));
    document.getElementById('btn-back').addEventListener('click', () => navigateTo(currentPage - 1));
    document.getElementById('btn-submit').addEventListener('click', submitProfile);

    const recordBtn = document.getElementById('btn-record');
    recordBtn.addEventListener('mousedown', startRecording);
    recordBtn.addEventListener('mouseup', stopRecording);
    recordBtn.addEventListener('mouseleave', stopRecording);
    recordBtn.addEventListener('touchstart', (e) => { e.preventDefault(); startRecording(); });
    recordBtn.addEventListener('touchend', stopRecording);
    
    document.getElementById('btn-rerecord').addEventListener('click', resetVoiceUI);
    document.getElementById('btn-confirm-voice').addEventListener('click', confirmVoice);
}

function updateAgeDisplay() {
    const dob = document.getElementById('child-dob').value;
    const ageDisplay = document.getElementById('age-display');
    if (!dob) {
        ageDisplay.innerText = '';
        return;
    }
    const birthDate = new Date(dob);
    const today = new Date();
    let age = today.getFullYear() - birthDate.getFullYear();
    const m = today.getMonth() - birthDate.getMonth();
    if (m < 0 || (m === 0 && today.getDate() < birthDate.getDate())) {
        age--;
    }
    ageDisplay.innerText = `${age} years old`;
}

function validatePage(page) {
    if (page === 1) {
        const name = document.getElementById('child-name').value.trim();
        const dob = document.getElementById('child-dob').value;
        if (!name || !dob) {
            alert('Please fill in the required fields (Name and Date of Birth).');
            return false;
        }
    }
    return true;
}

function navigateTo(newPage) {
    if (newPage > currentPage && !validatePage(currentPage)) return;
    if (newPage < 1 || newPage > 4) return;
    
    if (newPage === 4) {
        updateRecordingPrompt();
    }

    const currentDiv = document.getElementById(`page-${currentPage}`);
    const nextDiv = document.getElementById(`page-${newPage}`);

    currentDiv.classList.add('page-exit');
    setTimeout(() => {
        currentDiv.classList.remove('active', 'page-exit');
        nextDiv.classList.add('active');
    }, 300);

    currentPage = newPage;
    updateProgressBar();
    updateNavButtons();
}

function updateProgressBar() {
    const steps = document.querySelectorAll('.step');
    const lines = document.querySelectorAll('.step-line');
    
    steps.forEach((step, index) => {
        const stepNum = index + 1;
        step.classList.remove('active', 'completed');
        if (stepNum < currentPage) step.classList.add('completed');
        else if (stepNum === currentPage) step.classList.add('active');
    });

    lines.forEach((line, index) => {
        line.classList.toggle('completed', index < currentPage - 1);
    });
}

function updateNavButtons() {
    const btnBack = document.getElementById('btn-back');
    const btnNext = document.getElementById('btn-next');
    const btnSubmit = document.getElementById('btn-submit');

    btnBack.classList.toggle('hidden', currentPage === 1);
    btnNext.classList.toggle('hidden', currentPage === 4);
    btnSubmit.classList.toggle('hidden', currentPage !== 4);
}

function updateRecordingPrompt() {
    const name = document.getElementById('child-name').value.trim() || 'friend';
    const interests = formData.interests;
    const interest = interests.length > 0 ? interests[Math.floor(Math.random() * interests.length)] : 'learning new things';
    
    document.getElementById('voice-prompt').innerText = `Hi KODA! My name is ${name} and I love ${interest}!`;
}

async function startRecording() {
    if (document.getElementById('voice-result').classList.contains('hidden') === false) return;
    
    try {
        mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
        audioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
        source = audioContext.createMediaStreamSource(mediaStream);
        processor = audioContext.createScriptProcessor(4096, 1, 1);

        pcmData = [];
        
        processor.onaudioprocess = (e) => {
            const inputData = e.inputBuffer.getChannelData(0);
            let sum = 0;
            for (let i = 0; i < inputData.length; i++) {
                const sample = Math.max(-1, Math.min(1, inputData[i]));
                pcmData.push(sample < 0 ? sample * 0x8000 : sample * 0x7FFF);
                sum += inputData[i] * inputData[i];
            }
            
            const rms = Math.sqrt(sum / inputData.length);
            const volume = Math.min(100, rms * 1000);
            document.getElementById('volume-fill').style.width = `${volume}%`;
        };

        source.connect(processor);
        processor.connect(audioContext.destination);

        const orb = document.getElementById('voice-orb');
        const countdown = document.getElementById('countdown');
        const meter = document.getElementById('volume-meter');
        const btn = document.getElementById('btn-record');
        
        orb.classList.add('recording');
        countdown.classList.remove('hidden');
        meter.classList.remove('hidden');
        btn.classList.add('recording');
        btn.innerText = 'Recording...';
        
        let timeLeft = 10;
        countdown.innerText = timeLeft;
        
        recordingInterval = setInterval(() => {
            timeLeft--;
            if (timeLeft > 0) {
                countdown.innerText = timeLeft;
            } else {
                stopRecording();
            }
        }, 1000);
        
    } catch (err) {
        console.error('Microphone error:', err);
        alert('Could not access microphone. Please ensure permissions are granted.');
    }
}

function stopRecording() {
    if (!recordingInterval) return;
    
    clearInterval(recordingInterval);
    recordingInterval = null;
    
    if (processor) {
        processor.disconnect();
        source.disconnect();
        processor = null;
        source = null;
    }
    
    if (mediaStream) {
        mediaStream.getTracks().forEach(track => track.stop());
        mediaStream = null;
    }
    
    if (audioContext && audioContext.state !== 'closed') {
        audioContext.close();
    }

    const orb = document.getElementById('voice-orb');
    const countdown = document.getElementById('countdown');
    const meter = document.getElementById('volume-meter');
    const btn = document.getElementById('btn-record');
    
    orb.classList.remove('recording');
    countdown.classList.add('hidden');
    meter.classList.add('hidden');
    btn.classList.remove('recording');
    btn.innerText = hasExistingVoice ? 'Hold to Re-record Voice' : 'Hold to Record';
    
    if (pcmData.length > 0) {
        const buffer = new Int16Array(pcmData);
        voiceRecording = buffer;
        document.getElementById('btn-record').parentElement.classList.add('hidden');
        document.getElementById('voice-result').classList.remove('hidden');
    }
}

function resetVoiceUI() {
    voiceRecording = null;
    pcmData = [];
    document.getElementById('voice-result').classList.add('hidden');
    document.getElementById('btn-record').parentElement.classList.remove('hidden');
    document.getElementById('btn-submit').disabled = hasExistingVoice ? false : true;
    document.getElementById('volume-fill').style.width = '0%';
}

function confirmVoice() {
    document.getElementById('btn-confirm-voice').innerText = 'Confirmed';
    document.getElementById('btn-confirm-voice').disabled = true;
    document.getElementById('btn-rerecord').disabled = true;
    document.getElementById('btn-submit').disabled = false;
}

function collectFormData() {
    const pronounsSelect = document.getElementById('child-pronouns').value;
    const finalPronouns = pronounsSelect === 'custom' ? document.getElementById('custom-pronouns').value : pronounsSelect;

    return {
        personal: {
            name: document.getElementById('child-name').value.trim(),
            dob: document.getElementById('child-dob').value,
            pronouns: finalPronouns || 'they/them'
        },
        personality: formData.personality,
        interests: formData.interests,
        sibling_info: document.getElementById('sibling-info').value,
        neurodiversity: formData.neuro,
        neuro_context: document.getElementById('neuro-context').value,
        speech_goals: document.getElementById('speech-goals').value,
        pronunciation: formData.pronunciation,
        notes: document.getElementById('additional-notes').value,
        health: {
            allergies: document.getElementById('allergies').value,
            routines: document.getElementById('routines').value
        }
    };
}

async function submitProfile() {
    if (!voiceRecording && !hasExistingVoice) {
        alert('Please record a voice sample before finishing.');
        return;
    }

    const data = collectFormData();
    
    try {
        const btn = document.getElementById('btn-submit');
        const origText = btn.innerText;
        btn.innerText = 'Saving Profile...';
        btn.disabled = true;

        // Step 1: Save profile data
        const profileRes = await fetch('/api/profile', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });

        if (!profileRes.ok) {
            const err = await profileRes.json();
            throw new Error(err.error || 'Failed to save profile');
        }

        // Step 2: Enroll voice biometric ONLY if a new recording was made
        if (voiceRecording) {
            const voiceBuffer = voiceRecording.buffer;
            const voiceRes = await fetch('/api/voice-enroll', {
                method: 'POST',
                headers: { 'Content-Type': 'application/octet-stream' },
                body: voiceBuffer
            });

            if (!voiceRes.ok) {
                const err = await voiceRes.json();
                throw new Error(err.error || 'Failed to enroll voice');
            }
        }

        // Success — redirect to main KODA page
        btn.innerText = '✅ Profile Saved!';
        setTimeout(() => {
            window.location.href = '/';
        }, 1500);

    } catch (err) {
        console.error('Profile creation error:', err);
        alert('Error: ' + err.message);
        const btn = document.getElementById('btn-submit');
        btn.innerText = hasExistingVoice ? 'Update Profile & Start KODA' : 'Create Profile & Start KODA';
        btn.disabled = false;
    }
}

// Initial setup
document.getElementById('btn-submit').disabled = true;

