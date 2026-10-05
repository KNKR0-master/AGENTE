import shutil
import unittest

from tests import test_publisher_queue_ui as fixtures


COMMON = """
const snapshot={drafts:[
 {id:1,batch_id:20,media_asset_id:101,product_id:1,product_name:'Perfume',filename:'G1C1A1.mp4',folder_name:'Body Splash',caption:'Legenda A',status:'DRAFT',queue_position:1,queue_display:{label:'Pendente',kind:'neutral'}},
 {id:2,batch_id:20,media_asset_id:102,product_id:2,product_name:'Escova',filename:'attention.mp4',status:'MANUAL_ACTION_REQUIRED',upload_status:'TRANSFER_UNKNOWN',queue_position:2,queue_display:{label:'Envio a verificar',kind:'attention',message:'Não envie novamente'}},
 {id:3,batch_id:21,media_asset_id:103,product_id:1,filename:'saved.mp4',status:'PUBLISHED',persistence_status:'PERSISTED',queue_display:{label:'Publicado no Studio',kind:'ready'}},
 {id:4,batch_id:22,media_asset_id:104,product_id:2,filename:'running.mp4',status:'PREPARING',queue_position:1,queue_display:{label:'Preparando',kind:'running'}},
 {id:5,batch_id:23,media_asset_id:105,product_id:2,filename:'failed.mp4',status:'FAILED',queue_position:1,queue_display:{label:'Preparação interrompida',kind:'attention'}}
],executions:[],execution_items:[],queue_presentation:{targets:[],history:[]}};
const history=[30,31].map(id=>({id,status:'COMPLETED',items:[{...snapshot.drafts[2],id:undefined,draft_id:3,draft_batch_id:21,item_status:'SUCCEEDED',execution_id:id}]}));
const target=(type,id)=>({target_type:type,target_id:id,hidden_at:'2026-10-05T10:00:00Z'});
const ids=groups=>groups.flatMap(group=>group.drafts.map(item=>item.id));
"""


@unittest.skipUnless(shutil.which('node'), 'node required')
class QueueVisibilityUITests(unittest.TestCase):
    evaluate = fixtures.QueueUITests.evaluate

    def check(self, source):
        return self.evaluate(COMMON + source)

    def test_default_visible_and_critical_hidden_remains_attention(self):
        result = self.check("""
const before=ids(ui.queueVisibleGroups(snapshot));
snapshot.queue_presentation.targets=[target('DRAFT',2)];
const hidden=ui.queueHiddenGroups(snapshot,history).flatMap(group=>group.items);
console.log(JSON.stringify({before,after:ids(ui.queueVisibleGroups(snapshot)),hidden:hidden.map(item=>({id:item.id,kind:ui.queueIdentity(item).display.kind,status:item.status})),original:snapshot.drafts[1].status}));
""")
        self.assertEqual(set(result['before']), {1, 2, 4, 5})
        self.assertNotIn(2, result['after'])
        self.assertEqual(result['hidden'], [{'id': 2, 'kind': 'attention', 'status': 'MANUAL_ACTION_REQUIRED'}])
        self.assertEqual(result['original'], 'MANUAL_ACTION_REQUIRED')

    def test_parent_or_individual_hide_and_restore_composition(self):
        result = self.check("""
snapshot.queue_presentation.targets=[target('BATCH',20),target('DRAFT',1)];
const hidden=ids(ui.queueVisibleGroups(snapshot));
snapshot.queue_presentation.targets=snapshot.queue_presentation.targets.filter(row=>row.target_type!=='BATCH');
const restored=ids(ui.queueVisibleGroups(snapshot));
console.log(JSON.stringify({hidden,restored,remaining:snapshot.queue_presentation.targets,order:ui.queueGroups(snapshot).find(group=>group.batchId===20).all.map(item=>item.id)}));
""")
        self.assertNotIn(1, result['hidden'])
        self.assertNotIn(2, result['hidden'])
        self.assertNotIn(1, result['restored'])
        self.assertIn(2, result['restored'])
        self.assertEqual(result['remaining'][0]['target_type'], 'DRAFT')
        self.assertEqual(result['order'], [1, 2])

    def test_history_execution_hide_preserves_composite_identity(self):
        result = self.check("""
snapshot.queue_presentation.targets=[target('EXECUTION',30)];
const visible=ui.queueHistoryExecutions(snapshot,history);
const hidden=ui.queueHiddenGroups(snapshot,history);
snapshot.queue_presentation.targets.push(target('DRAFT',3));
const bothHidden=ui.queueHiddenGroups(snapshot,history).filter(group=>group.source==='history');
snapshot.queue_presentation.targets=snapshot.queue_presentation.targets.filter(row=>row.target_type!=='EXECUTION');
console.log(JSON.stringify({visible:visible.map(e=>e.id),hidden:hidden.map(group=>group.execution?.id),keys:bothHidden.flatMap(group=>group.items.map(item=>`${group.execution.id}:${item.draft_id}`)),afterParentRestore:ui.queueHistoryExecutions(snapshot,history).length}));
""")
        self.assertEqual(result['visible'], [31])
        self.assertEqual(result['hidden'], [30])
        self.assertEqual(result['keys'], ['30:3', '31:3'])
        self.assertEqual(result['afterParentRestore'], 0)

    def test_filters_search_product_batch_status_attention_combinations_reset(self):
        result = self.check("""
const query=filters=>ids(ui.queueVisibleGroups(snapshot,filters));
console.log(JSON.stringify({search:query({search:'body splash'}),product:query({product:'1'}),batch:query({batch:'20'}),ready:query({status:'READY'}),running:query({status:'RUNNING'}),attention:query({attention:'REQUIRED'}),clear:query({attention:'CLEAR'}),failed:query({status:'FAILED'}),combined:query({product:'2',batch:'20',attention:'REQUIRED'}),reset:query({}),saved:ui.queueHistoryExecutions(snapshot,history,{status:'SAVED'}).length}));
""")
        self.assertEqual(result['search'], [1])
        self.assertEqual(result['product'], [1])
        self.assertEqual(result['batch'], [1, 2])
        self.assertEqual(result['ready'], [1])
        self.assertEqual(result['running'], [4])
        self.assertEqual(set(result['attention']), {2, 5})
        self.assertEqual(set(result['clear']), {1, 4})
        self.assertEqual(result['failed'], [5])
        self.assertEqual(result['combined'], [2])
        self.assertEqual(set(result['reset']), {1, 2, 4, 5})
        self.assertEqual(result['saved'], 2)

    def test_exact_deep_link_active_history_hidden_draft_batch_execution(self):
        result = self.check("""
const locate=(id,media,batch)=>ui.queueDeepLinkLocation(snapshot,history,id,media,batch);
const active=locate(1,101,20),historic=locate(3,103,21),wrong=locate(1,102,20);
snapshot.queue_presentation.targets=[target('DRAFT',1)];const draftHidden=locate(1,101,20);
snapshot.queue_presentation.targets=[target('BATCH',20)];const batchHidden=locate(1,101,20);
snapshot.queue_presentation.targets=[target('EXECUTION',31)];const executionHidden=locate(3,103,21);
console.log(JSON.stringify({active,historic,wrong,draftHidden,batchHidden,executionHidden}));
""")
        self.assertEqual(result['active']['view'], 'ACTIVE')
        self.assertEqual(result['historic']['view'], 'HISTORY')
        self.assertEqual(result['historic']['executionId'], 31)
        self.assertIsNone(result['wrong'])
        for key in ('draftHidden', 'batchHidden', 'executionHidden'):
            self.assertEqual(result[key]['view'], 'HIDDEN')
        self.assertEqual(result['executionHidden']['executionId'], 31)

    def test_hidden_filters_do_not_restore_or_change_targets(self):
        result = self.check("""
snapshot.queue_presentation.targets=[target('BATCH',20)];
const before=JSON.stringify(snapshot);
const filtered=ui.queueHiddenGroups(snapshot,history,{search:'attention',attention:'REQUIRED'});
console.log(JSON.stringify({ids:filtered.flatMap(group=>group.items.map(item=>item.id)),unchanged:before===JSON.stringify(snapshot)}));
""")
        self.assertEqual(result['ids'], [2])
        self.assertTrue(result['unchanged'])

    def test_hidden_parent_disables_reordering_without_changing_operational_order(self):
        result = self.check("""
snapshot.drafts[0].queue_capabilities={can_reorder:true};snapshot.drafts[1].queue_capabilities={can_reorder:true};
snapshot.queue_presentation.targets=[target('DRAFT',1)];
const group=ui.queueVisibleGroups(snapshot).find(group=>group.batchId===20);
console.log(JSON.stringify({all:group.all.map(item=>item.id),visible:group.drafts.map(item=>item.id),fullyVisible:group.fullyVisible,reorder:ui.reorderedIds(group,2,1)}));
""")
        self.assertEqual(result['all'], [1, 2])
        self.assertEqual(result['visible'], [2])
        self.assertFalse(result['fullyVisible'])
        self.assertIsNone(result['reorder'])

    def test_failed_execution_remains_attention_when_hidden(self):
        result = self.check("""
snapshot.executions=[{id:44,batch_id:20,status:'PAUSED'}];
snapshot.execution_items=[{execution_id:44,draft_id:1,execution_position:1,status:'FAILED'}];
snapshot.drafts[0].status='READY_FOR_REVIEW';snapshot.drafts[0].queue_display={label:'Salvo no TikTok',kind:'ready'};
snapshot.queue_presentation.targets=[target('DRAFT',1)];
const item=ui.queueHiddenGroups(snapshot,history).flatMap(group=>group.items).find(row=>row.id===1);
console.log(JSON.stringify({display:item.queue_display,original:snapshot.drafts[0].status}));
""")
        self.assertEqual(result['display']['kind'], 'attention')
        self.assertEqual(result['display']['label'], 'Falhou nesta execução')
        self.assertEqual(result['original'], 'READY_FOR_REVIEW')


if __name__ == '__main__':
    unittest.main()
