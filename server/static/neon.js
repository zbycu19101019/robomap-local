(() => {
 'use strict';
 const $ = id => document.getElementById(id);
 let state = null, lastNeon = null;
 const say = text => { if (!window.speechSynthesis) return; speechSynthesis.cancel(); const u = new SpeechSynthesisUtterance(text); u.lang='pl-PL'; u.rate=.95; speechSynthesis.speak(u); };
 async function api(path, method='GET') { const r=await fetch(path,{method,cache:'no-store',...(method==='POST'?{headers:{'Content-Type':'application/json'},body:'{}'}:{})}); const d=await r.json(); if(!r.ok)throw Error(d.detail||'Brak odpowiedzi');return d; }
 const toast = text => { $('toast').textContent=text; $('toast').classList.add('show'); setTimeout(()=>$('toast').classList.remove('show'),4500); };
 window.addEventListener('robomap-status', ({detail:s}) => {
   state=s; const alarm=s?.state==='error';
   document.body.classList.toggle('alarm',alarm); document.body.classList.toggle('cleaning',s?.state==='cleaning');
   $('hudAlarm').classList.toggle('hidden',!alarm); $('hudFault').textContent=s?.error_name||'Robot zgłosił błąd — sprawdź urządzenie';
   const labels={cleaning:'Procedura czyszczenia',paused:'Chwila oddechu',idle:'Gotowy do działania',charging:'Uzupełniam energię',charged:'Energia na pełnym poziomie',returning:'Kurs na bazę',offline:'Utracono połączenie',unconfigured:'Szukamy Twojego E5','awaiting-token':'Połącz swoje konto Xiaomi',error:'Wymagana Twoja pomoc'};
   $('hudTitle').textContent=labels[s?.state]||'Witaj na pokładzie';
   $('hudState').textContent=alarm?'ALERT / WYMAGANA INTERWENCJA':s?.state==='awaiting-token'?'KONFIGURACJA / KROK 2 Z 3':s?.state==='offline'?'ŁĄCZNOŚĆ / OFFLINE':'TELEMETRIA JEDNOSTKI';
   $('hudDetail').textContent=s?.state==='awaiting-token'?'E5 jest odnaleziony. Otwórz Ustawienia i połącz konto, aby dokończyć automatyczną konfigurację.':s?.state==='cleaning'?'System aktywny. Czas sprzątania zasila doświadczenie jednostki.':s?.detail||'Oczekiwanie na telemetrię.';
   $('hudBattery').textContent=s?.battery==null?'—':s.battery+'%';
   $('batteryArc').setAttribute('stroke-dasharray',`${Math.max(0,Math.min(100,s?.battery||0))} 100`);
   $('batteryArc').setAttribute('stroke',s?.battery<20?'#ff526b':s?.battery<50?'#ffcc62':'#4affb1');
 });
 async function refresh(){
   try{lastNeon=await api('/api/neon');const {rpg,cat,halted,radar,localization}=lastNeon;
     $('xpMinutes').textContent=Math.floor(rpg.seconds/60)+' min'; $('xpLevel').textContent=String(rpg.level).padStart(2,'0');$('xpValue').textContent=rpg.exp+' EXP';$('xpRank').textContent=rpg.rank;$('xpProgress').value=rpg.progress;
     $('xpNext').textContent=rpg.next_exp?`${rpg.next_exp-rpg.exp} EXP do kolejnego poziomu`:'Osiągnięto najwyższą rangę';
     $('achievements').replaceChildren(...['Nocny Marek','Maratończyk'].map(name=>{const el=document.createElement('span');const ok=rpg.achievements.includes(name);el.textContent=(ok?'◆ ':'◇ ')+name;el.classList.toggle('earned',ok);return el;}));
     $('catStatus').textContent=cat.error|| (cat.active?`Aktywny · ${cat.remaining} s do zakończenia`:'Tryb nieaktywny');$('catStart').disabled=cat.active||halted;$('unlockControl').classList.toggle('hidden',!halted);
     const speed=Math.round((localization?.linear_speed_mps||.1)*1000)/10;
     $('radarNote').textContent=`Pozycja RoboMap: ${localization?.calibrated?'skalibrowana':'bez kotwicy'} · jazda ${speed} cm/s · ${Math.round(localization?.turn_rate_dps||90)}°/s. `+(radar.bumper_supported?'Kropki oznaczają zgłoszony błąd zablokowanego zderzaka.':'Oczekiwanie na odczyt usterek zderzaka.');
     drawRadar(radar);
   }catch(e){$('catStatus').textContent='Brak połączenia z serwerem';}finally{setTimeout(refresh,2200);}
 }
 function drawRadar(radar){const c=$('radarCanvas'),x=c.getContext('2d'),w=c.width,h=c.height;x.clearRect(0,0,w,h);x.strokeStyle='#173039';x.lineWidth=1;for(let i=0;i<w;i+=35){x.beginPath();x.moveTo(i,0);x.lineTo(i,h);x.stroke();}for(let i=0;i<h;i+=35){x.beginPath();x.moveTo(0,i);x.lineTo(w,i);x.stroke();}const points=[radar.pose,...radar.dots];const extent=Math.max(1,...points.flatMap(p=>[Math.abs(p.x),Math.abs(p.y)]));const scale=Math.min(w,h)*.4/extent;for(const p of radar.dots){const px=w/2+p.x*scale,py=h/2+p.y*scale;const g=x.createRadialGradient(px,py,0,px,py,16);g.addColorStop(0,'#ff4b5f');g.addColorStop(1,'#ff4b5f00');x.fillStyle=g;x.fillRect(px-16,py-16,32,32);}x.save();x.translate(w/2+radar.pose.x*scale,h/2+radar.pose.y*scale);x.rotate(radar.pose.heading);x.fillStyle='#00f3ff';x.beginPath();x.moveTo(0,-9);x.lineTo(6,7);x.lineTo(0,4);x.lineTo(-6,7);x.closePath();x.fill();x.restore();x.fillStyle='#6e9ba9';x.font='11px Consolas';x.fillText(`EST / X ${radar.pose.x.toFixed(2)} m   Y ${radar.pose.y.toFixed(2)} m`,15,22);}
 for(const [id,path] of [['catStart','/api/cat/start'],['catStop','/api/cat/stop'],['globalStop','/api/robot/emergency-stop'],['unlockControl','/api/control/unlock'],['radarReset','/api/radar/reset']])$(id).addEventListener('click',async()=>{try{const result=await api(path,'POST');toast(result.detail||'Polecenie przyjęte');}catch(e){toast(e.message);}});
 const Recognition=window.SpeechRecognition||window.webkitSpeechRecognition;
 let recognition=null;
 $('voiceButton').addEventListener('click',async()=>{
   if(recognition){recognition.abort();return;}
   if(!Recognition){$('voiceStatus').textContent='Ta przeglądarka nie obsługuje rozpoznawania mowy. Użyj przycisków lub otwórz panel w Edge/Chrome.';return;}
   const r=new Recognition();r.lang='pl-PL';r.interimResults=false;r.continuous=false;
   if(!$('voiceCloud').checked){
     if(!('processLocally' in r)||!Recognition.available){$('voiceStatus').textContent='Lokalna mowa niedostępna. Możesz zezwolić poniżej na usługę mowy przeglądarki (może wysyłać głos do chmury).';return;}
     try {if(await Recognition.available({langs:['pl-PL'],processLocally:true})!=='available'){$('voiceStatus').textContent='Brak lokalnego pakietu polskiej mowy. Włącz usługę przeglądarki lub użyj pilota.';return;}}catch(e){$('voiceStatus').textContent='Lokalna mowa niedostępna: '+e.message;return;}
     r.processLocally=true;
   }
   recognition=r;r.onstart=()=>{$('voiceButton').classList.add('listening');$('voiceStatus').textContent='Nasłuchuję…';};r.onend=()=>{recognition=null;$('voiceButton').classList.remove('listening');};
   r.onerror=e=>{$('voiceStatus').textContent='Mikrofon: '+e.error;};
   r.onresult=async e=>{const text=e.results[0][0].transcript;const action=parseVoice(text);$('voiceStatus').textContent='„'+text+'”';if(!action){say('Nie rozpoznałam polecenia.');return;}try{await api('/api/robot/'+action,'POST');say(action==='start'?'Zrozumiałam, rozpoczynam procedurę czyszczenia.':action==='dock'?'Zrozumiałam, wracam do bazy.':'Zrozumiałam, wysłałam polecenie zatrzymania.');}catch(err){say('Nie udało się wykonać polecenia.');toast(err.message);}};
   try{r.start();}catch(e){recognition=null;$('voiceStatus').textContent=e.message;}
 });
 function parseVoice(text){const t=text.toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g,'').replace(/ł/g,'l');if(/\b(stop|zatrzymaj|stoj)\b/.test(t))return 'emergency-stop';if(/\bnie\b/.test(t))return null;if(/\b(pauz\w*|wstrzymaj)\b/.test(t))return 'pause';if(/\b(baz\w*|wrac\w*|wroc\w*)\b/.test(t))return 'dock';if(/\b(start\w*|sprzataj\w*|sprzatan\w*|odkurzaj\w*)\b/.test(t))return 'start';return null;}
 window.robomapParseVoice=parseVoice;
 let soundOn=localStorage.getItem('neonSound')==='true',audio;
 const sound=document.createElement('button');sound.className='sound-toggle';const label=()=>sound.textContent=soundOn?'Dźwięk: wł.':'Dźwięk: wył.';label();document.querySelector('.header-status').append(sound);sound.onclick=()=>{soundOn=!soundOn;localStorage.setItem('neonSound',String(soundOn));label();};
 document.addEventListener('click',e=>{if(!soundOn||!e.target.closest('button'))return;try{audio ||= new (window.AudioContext||window.webkitAudioContext)();audio.resume();const o=audio.createOscillator(),g=audio.createGain();o.type='sine';o.frequency.setValueAtTime(650,audio.currentTime);o.frequency.exponentialRampToValueAtTime(380,audio.currentTime+.08);g.gain.setValueAtTime(.025,audio.currentTime);g.gain.exponentialRampToValueAtTime(.001,audio.currentTime+.1);o.connect(g).connect(audio.destination);o.start();o.stop(audio.currentTime+.11);}catch{}});
 let schedules=[];
 function renderSchedules(){ $('scheduleList').replaceChildren(...schedules.map(entry=>{const row=document.createElement('div');row.className='schedule-row';const label=document.createElement('span');label.textContent=entry.time+' · '+entry.days.map(d=>['Pn','Wt','Śr','Cz','Pt','So','Nd'][d]).join(', ')+' · '+({start:'Start',dock:'Baza',pause:'Pauza'}[entry.action]);const remove=document.createElement('button');remove.textContent='Usuń';remove.onclick=async()=>{try{await saveSchedules(schedules.filter(e=>e.id!==entry.id));}catch(e){toast(e.message);}};row.append(label,remove);return row;})); }
 async function saveSchedules(entries){const r=await fetch('/api/schedule',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({entries})});const d=await r.json();if(!r.ok)throw Error(typeof d.detail==='string'?d.detail:'Sprawdź harmonogram');schedules=d.entries;renderSchedules();}
 $('scheduleForm').addEventListener('submit',async e=>{e.preventDefault();const days=[...document.querySelectorAll('[name=scheduleDay]:checked')].map(x=>Number(x.value));if(!days.length){toast('Wybierz co najmniej jeden dzień.');return;}try{await saveSchedules([...schedules,{id:Date.now().toString(36),time:$('scheduleTime').value,days,action:$('scheduleAction').value,enabled:true}]);toast('Zapisano termin');}catch(err){toast(err.message);}});
 document.querySelectorAll('[data-profile]').forEach(b=>b.onclick=async()=>{try{await api('/api/profiles/'+b.dataset.profile,'POST');toast('Profil zastosowany');}catch(e){toast(e.message);}});
 api('/api/schedule').then(d=>{schedules=d.entries;renderSchedules();}).catch(e=>toast(e.message));
 refresh();
})();
