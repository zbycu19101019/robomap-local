(()=>{
 const panel=document.querySelector('[data-panel="automations"]');
 const section=document.createElement('section');section.className='more-tools';
 section.innerHTML=`<h3>Twoje reguły</h3><p class="muted">Każda reguła wykonuje się najwyżej raz dziennie, gdy RoboMap jest włączony. Pominięcia i wyniki znajdziesz w historii. Czas sprzątania liczony jest od obserwacji przez aplikację; przerwa lub restart zeruje licznik.</p>
 <details><summary>Dodaj lub edytuj regułę</summary><form id="ruleForm">
 <label>Nazwa<input id="ruleName" required maxlength="80" placeholder="Np. Powrót przy niskiej baterii"></label>
 <label>Gdy<select id="ruleTrigger"><option value="time">Nadejdzie godzina</option><option value="battery_below">Bateria podczas sprzątania spadnie poniżej</option><option value="cleaning_minutes">Sprzątanie trwa co najmniej</option></select></label>
 <label id="ruleTimeLabel">Godzina<input id="ruleTime" type="time" value="09:00" required></label>
 <label id="ruleThresholdLabel" hidden>Próg (% baterii lub minuty)<input id="ruleThreshold" type="number" min="1" max="240" value="20" required></label>
 <div class="schedule-days">${['Pn','Wt','Śr','Cz','Pt','So','Nd'].map((d,i)=>`<label><input type="checkbox" name="ruleDay" value="${i}" checked>${d}</label>`).join('')}</div>
 <label>Wykonaj<select id="ruleAction"><option value="start">Rozpocznij sprzątanie</option><option value="pause">Wstrzymaj sprzątanie</option><option value="dock">Wróć do bazy</option><option value="quiet">Moc: Cicha</option><option value="standard">Moc: Standard</option><option value="strong">Moc: Mocna</option></select></label>
 <label>Minimalna bateria dla START (%)<input id="ruleBattery" type="number" min="0" max="100" value="30"></label>
 <label class="check-row"><input id="ruleQuiet" type="checkbox" checked>Uwzględniaj ciszę nocną</label>
 <label class="check-row"><input id="ruleEnabled" type="checkbox">Włącz tę regułę</label>
 <button class="primary" type="submit">Zapisz regułę</button><button id="ruleCancel" type="button">Nowa / anuluj edycję</button></form></details>
 <p id="ruleMessage" role="status"></p><button id="rulesReload" type="button">Odśwież reguły</button><div id="rulesList"></div>`;
 panel.append(section);
 const $=id=>document.getElementById(id);let doc={revision:0,rules:[]},editing=null,busy=false;
 const actions={start:'START',pause:'Pauza',dock:'Baza',quiet:'Moc Cicha',standard:'Moc Standard',strong:'Moc Mocna'};
 async function request(method,body){const r=await fetch('/api/rules',{method,headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});const d=await r.json();if(!r.ok)throw Error(typeof d.detail==='string'?d.detail:'Sprawdź dni, próg i wybraną akcję.');return d;}
 async function load(){try{doc=await request('GET');render();}catch(e){$('ruleMessage').textContent=e.message;}}
 async function save(rules){if(busy)return;busy=true;try{doc=await request('PUT',{revision:doc.revision,rules});render();$('ruleMessage').textContent='Reguły zapisane.';return true;}catch(e){$('ruleMessage').textContent=e.message;return false;}finally{busy=false;}}
 function mode(){const timed=$('ruleTrigger').value==='time';$('ruleTimeLabel').hidden=!timed;$('ruleThresholdLabel').hidden=timed;}
 function reset(){editing=null;$('ruleForm').reset();mode();}
 function render(){const list=$('rulesList');list.replaceChildren();if(!doc.rules.length){list.textContent='Brak własnych reguł. Dodaj pierwszą powyżej.';return;}
 for(const r of doc.rules){const row=document.createElement('div');row.className='subcard';const title=document.createElement('strong');title.textContent=r.name;const info=document.createElement('p');info.className='muted';info.textContent=`${r.enabled?'Włączona':'Wyłączona'} · ${r.trigger==='time'?r.time:r.trigger==='battery_below'?'Bateria < '+r.threshold+'%':'Sprzątanie ≥ '+r.threshold+' min'} → ${actions[r.action]}`;row.append(title,info);
 const toggle=document.createElement('button');toggle.textContent=r.enabled?'Wyłącz':'Włącz';toggle.onclick=()=>save(doc.rules.map(v=>v.id===r.id?{...v,enabled:!v.enabled}:v));
 const edit=document.createElement('button');edit.textContent='Edytuj';edit.onclick=()=>{editing=r.id;$('ruleName').value=r.name;$('ruleTrigger').value=r.trigger;$('ruleTime').value=r.time;$('ruleThreshold').value=r.threshold;$('ruleAction').value=r.action;$('ruleBattery').value=r.min_battery;$('ruleQuiet').checked=r.respect_quiet;$('ruleEnabled').checked=r.enabled;section.querySelectorAll('[name=ruleDay]').forEach(c=>c.checked=r.days.includes(Number(c.value)));section.querySelector('details').open=true;mode();$('ruleForm').scrollIntoView({block:'center'});};row.append(toggle,edit);list.append(row);}}
 $('ruleForm').onsubmit=async e=>{e.preventDefault();const r={id:editing||'r-'+Date.now(),name:$('ruleName').value.trim(),enabled:$('ruleEnabled').checked,trigger:$('ruleTrigger').value,time:$('ruleTime').value,threshold:Number($('ruleThreshold').value),days:[...section.querySelectorAll('[name=ruleDay]:checked')].map(c=>Number(c.value)),action:$('ruleAction').value,min_battery:Number($('ruleBattery').value),respect_quiet:$('ruleQuiet').checked};if(await save(editing?doc.rules.map(v=>v.id===editing?r:v):[...doc.rules,r]))reset();};
 $('ruleTrigger').onchange=mode;$('ruleCancel').onclick=reset;$('rulesReload').onclick=load;mode();load();
})();
