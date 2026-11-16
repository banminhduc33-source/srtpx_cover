/**
 * Google Apps Script — theo dõi thư mục Drive "srtpx/in" mỗi phút.
 * Thấy đủ cặp (video + .srtpx cùng tên) thì chuyển sang "processing" và gọi GitHub Actions.
 *
 * Cài đặt: xem HUONG_DAN_DRIVE.md. Chạy hàm setup() một lần.
 * Script Properties cần có: GH_REPO (vd "tenban/srtpx_cover"), GH_TOKEN (fine-grained PAT, quyền Contents: Read and write).
 */
const CFG = {
  ROOT_NAME: 'srtpx',
  VIDEO_EXT: ['mp4', 'mov', 'mkv', 'webm', 'm4v', 'avi'],
  SETTLE_SECONDS: 20,          // chờ file "yên" ít nhất ngần này giây rồi mới lấy
  EVENT_TYPE: 'srtpx-export',
};

function setup() {
  const props = PropertiesService.getScriptProperties();
  if (typeof EMBED !== 'undefined') props.setProperties(EMBED);   // file do Cai_Dat_Mot_Phat tạo sẵn thông số
  if (!props.getProperty('GH_REPO') || !props.getProperty('GH_TOKEN')) {
    throw new Error('Hãy thêm GH_REPO và GH_TOKEN trong Project Settings > Script Properties trước.');
  }
  let root;
  const it = DriveApp.getRootFolder().getFoldersByName(CFG.ROOT_NAME);
  root = it.hasNext() ? it.next() : DriveApp.getRootFolder().createFolder(CFG.ROOT_NAME);
  ['in', 'processing', 'out', 'done', 'failed'].forEach(n => sub_(root, n, true));
  props.setProperty('ROOT_ID', root.getId());
  ScriptApp.getProjectTriggers().forEach(t => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('scan').timeBased().everyMinutes(1).create();
  Logger.log('Xong. Thư mục: ' + root.getUrl());
}

function sub_(root, name, create) {
  const it = root.getFoldersByName(name);
  if (it.hasNext()) return it.next();
  if (create) return root.createFolder(name);
  throw new Error('Thiếu thư mục con: ' + name);
}

function scan() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(5000)) return;
  try {
    const props = PropertiesService.getScriptProperties();
    const root = DriveApp.getFolderById(props.getProperty('ROOT_ID'));
    const inF = sub_(root, 'in'), procF = sub_(root, 'processing');

    const map = {};
    const files = inF.getFiles();
    const now = Date.now();
    while (files.hasNext()) {
      const f = files.next();
      if (now - f.getLastUpdated().getTime() < CFG.SETTLE_SECONDS * 1000) continue;
      const name = f.getName(), dot = name.lastIndexOf('.');
      if (dot < 1) continue;
      const base = name.slice(0, dot), ext = name.slice(dot + 1).toLowerCase();
      const e = map[base] || (map[base] = {});
      if (ext === 'srtpx') e.srt = f;
      else if (CFG.VIDEO_EXT.indexOf(ext) >= 0) e.vid = f;
    }

    const moved = [];
    Object.keys(map).forEach(base => {
      const e = map[base];
      if (e.srt && e.vid) { e.vid.moveTo(procF); e.srt.moveTo(procF); moved.push(e); }
    });
    if (!moved.length) return;

    const res = UrlFetchApp.fetch('https://api.github.com/repos/' + props.getProperty('GH_REPO') + '/dispatches', {
      method: 'post',
      contentType: 'application/json',
      headers: {
        Authorization: 'Bearer ' + props.getProperty('GH_TOKEN'),
        Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
      },
      payload: JSON.stringify({ event_type: CFG.EVENT_TYPE }),
      muteHttpExceptions: true,
    });
    if (res.getResponseCode() !== 204) {
      moved.forEach(e => { e.vid.moveTo(inF); e.srt.moveTo(inF); });   // trả về để lần quét sau thử lại
      throw new Error('GitHub trả về ' + res.getResponseCode() + ': ' + res.getContentText());
    }
  } finally {
    lock.releaseLock();
  }
}
