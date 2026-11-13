'use strict';

window.ScoutingVisitDrafts = (() => {
  let database;

  function open() {
    database ??= new Promise((resolve, reject) => {
      const request = indexedDB.open('agropulse-field', 1);
      request.onupgradeneeded = () => {
        request.result.createObjectStore('drafts', {keyPath: 'taskId'});
        request.result.createObjectStore('workspace');
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
      request.onblocked = () => reject(Error('Закройте другие вкладки AgroPulse и попробуйте снова.'));
    }).catch(error => {
      database = undefined;
      throw error;
    });
    return database;
  }

  async function transaction(name, mode, operation) {
    const db = await open();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(name, mode);
      let result;
      tx.oncomplete = () => resolve(result);
      tx.onabort = () => reject(tx.error || Error('Не удалось сохранить данные на устройстве'));
      operation(tx.objectStore(name), value => { result = value; });
    });
  }

  function read(name, operation) {
    return transaction(name, 'readonly', (store, done) => {
      const request = operation(store);
      request.onsuccess = () => done(request.result);
    });
  }

  // Check and update in one transaction so two tabs cannot enqueue competing
  // versions of the same observation. A queued snapshot is immutable.
  function change(taskId, update) {
    return transaction('drafts', 'readwrite', (store, done) => {
      const request = store.get(taskId);
      request.onsuccess = () => done(update(store, request.result));
    });
  }

  return {
    list: () => read('drafts', store => store.getAll()),
    get: taskId => read('drafts', store => store.get(taskId)),
    workspace: () => read('workspace', store => store.get('current')),
    saveWorkspace: state => {
      const {items, periods, snapshot_at, default_period, tasks} = state;
      const visitStatuses = state.visitStatuses ?? state.visits.map(({payload,recorded_at_utc}) => {
        const {task_id, request_id, observed_at_utc, outcome} = payload;
        return {task_id, request_id, observed_at_utc, outcome, recorded_at_utc};
      });
      return transaction('workspace', 'readwrite', store => {
        store.put({items, periods, snapshot_at, default_period, tasks, visitStatuses, visits:[], plans:[]}, 'current');
      });
    },
    save(draft, expectedRevision) {
      return change(draft.taskId, (store, current) => {
        if (current?.queued || current?.revision !== expectedRevision) return false;
        store.put(draft);
        return true;
      });
    },
    acknowledge(draft) {
      return change(draft.taskId, (store, current) => {
        if (current?.revision === draft.revision && current.queued) store.delete(draft.taskId);
      });
    },
    reject(draft, error) {
      return change(draft.taskId, (store, current) => {
        if (current?.revision === draft.revision && current.queued) {
          store.put({...current, queued:false, error});
        }
      });
    }
  };
})();
