import json
import shutil
import subprocess
import unittest
from pathlib import Path

from src.publisher.queue_policy import draft_queue_presentation


ROOT = Path(__file__).resolve().parents[1]
UI_STATE = ROOT / 'dashboard' / 'publisher_state.js'


@unittest.skipUnless(shutil.which('node'), 'node is required for queue UI tests')
class QueueUITests(unittest.TestCase):
    def evaluate(self, source):
        result = subprocess.run(
            [shutil.which('node'), '-e', f"const ui=require({json.dumps(str(UI_STATE))});\n{source}"],
            cwd=ROOT, capture_output=True, text=True, encoding='utf-8', check=True,
        )
        return json.loads(result.stdout)

    def test_batch_grouping_order_filter_and_mult_product_caption(self):
        result = self.evaluate("""
          const drafts=[
            {id:1,batch_id:7,queue_position:3,status:'DRAFT',filename:'third.mp4',product_id:1,product_name:'A',caption:'Original A',queue_capabilities:{can_reorder:true,can_edit:true,can_start:true}},
            {id:2,batch_id:7,queue_position:1,status:'DRAFT',filename:'first.mp4',product_id:2,product_name:'B',caption:'Original B',queue_capabilities:{can_reorder:true,can_edit:true,can_start:true}},
            {id:3,batch_id:7,queue_position:2,status:'DRAFT',filename:'second.mp4',product_id:1,product_name:'A',caption:'Second A',queue_capabilities:{can_reorder:true,can_edit:true,can_start:true}},
            {id:4,batch_id:8,queue_position:1,status:'DRAFT',filename:'other.mp4',product_id:3,product_name:'C',caption:'Other',queue_capabilities:{can_reorder:true}},
          ];
          const snapshot={drafts,queue_preflights:{7:{items:drafts.slice(0,3).map(d=>({draft_id:d.id,ready:true}))}},executions:[],execution_items:[]};
          const group=ui.queueGroups(snapshot,{batch:'7'})[0];
          const filtered=ui.queueGroups(snapshot,{search:'Second A'})[0];
          console.log(JSON.stringify({ids:group.drafts.map(d=>d.id),products:group.drafts.map(d=>d.product_id),captions:group.drafts.map(d=>d.caption),
            proposed:ui.reorderedIds(group,2,1),filtered:filtered.drafts.map(d=>d.id),filteredOrder:ui.reorderedIds(filtered,3,1),original:drafts.map(d=>d.queue_position)}));
        """)
        self.assertEqual(result['ids'], [2, 3, 1])
        self.assertEqual(result['products'], [2, 1, 1])
        self.assertEqual(result['captions'], ['Original B', 'Second A', 'Original A'])
        self.assertEqual(result['proposed'], [3, 1, 2])
        self.assertEqual(result['filtered'], [3])
        self.assertIsNone(result['filteredOrder'])
        self.assertEqual(result['original'], [3, 1, 2, 1])

    def test_execution_position_frozen_and_capability_guard(self):
        result = self.evaluate("""
          const drafts=[1,2,3].map(id=>({id,batch_id:5,queue_position:id,status:'DRAFT',queue_capabilities:{can_reorder:false,can_edit:id===1,can_remove:id===1,can_verify:id===2,remote_effect_possible:id===2}}));
          const snapshot={drafts,executions:[{id:9,batch_id:5,status:'PAUSED'}],execution_items:[
            {execution_id:9,batch_id:5,draft_id:1,execution_position:3},
            {execution_id:9,batch_id:5,draft_id:2,execution_position:1},
            {execution_id:9,batch_id:5,draft_id:3,execution_position:2}]};
          const group=ui.queueGroups(snapshot)[0];
          console.log(JSON.stringify({ids:group.drafts.map(d=>d.id),positions:group.drafts.map(d=>group.positions.get(d.id)),frozen:group.frozen,
            reorder:ui.reorderedIds(group,2,1),edit:ui.bulkAllowed([drafts[0]],'can_edit'),bulkEdit:ui.bulkAllowed(drafts.slice(0,2),'can_edit'),
            bulkRemove:ui.bulkAllowed(drafts.slice(0,2),'can_remove'),verify:ui.bulkAllowed([drafts[1]],'can_verify')}));
        """)
        self.assertEqual(result['ids'], [2, 3, 1])
        self.assertEqual(result['positions'], [1, 2, 3])
        self.assertTrue(result['frozen'])
        self.assertIsNone(result['reorder'])
        self.assertTrue(result['edit'])
        self.assertFalse(result['bulkEdit'])
        self.assertFalse(result['bulkRemove'])
        self.assertTrue(result['verify'])

    def test_active_queue_separates_pending_review_paused_and_completed_history(self):
        result = self.evaluate("""
          const caps={can_edit:true,can_reorder:true,can_remove:true,can_start:true};
          const drafts=[
            {id:17,batch_id:1,queue_position:2,status:'DRAFT',filename:'second.mp4',queue_capabilities:caps},
            {id:2,batch_id:1,queue_position:1,status:'DRAFT',filename:'first.mp4',queue_capabilities:caps},
            {id:40,batch_id:4,queue_position:1,status:'READY_FOR_REVIEW',filename:'review.mp4',persistence_status:'PERSISTED',queue_capabilities:{can_edit:false}},
            {id:9,batch_id:3,queue_position:1,status:'MANUAL_ACTION_REQUIRED',filename:'unknown.mp4',upload_status:'TRANSFER_UNKNOWN',queue_capabilities:{can_verify:true}},
            {id:11,batch_id:3,queue_position:2,status:'DRAFT',filename:'pending-in-execution.mp4',queue_capabilities:{can_start:false}},
            {id:50,batch_id:5,queue_position:1,status:'PUBLISHED',filename:'published.mp4',queue_capabilities:{}}
          ];
          const snapshot={drafts,executions:[
            {id:301,batch_id:4,status:'COMPLETED'},
            {id:300,batch_id:3,status:'PAUSED'}
          ],execution_items:[
            {execution_id:301,batch_id:4,draft_batch_id:4,draft_id:40,execution_position:1},
            {execution_id:300,batch_id:3,draft_batch_id:3,draft_id:9,execution_position:2},
            {execution_id:300,batch_id:3,draft_batch_id:3,draft_id:11,execution_position:1}
          ],queue_preflights:{1:{items:[{draft_id:2,ready:true},{draft_id:17,ready:true}]}}};
          const groups=ui.queueGroups(snapshot);
          console.log(JSON.stringify(groups.map(g=>({key:g.key,source:g.source,batchId:g.batchId,status:g.execution?.status,
            ids:g.drafts.map(d=>d.id),positions:g.drafts.map(d=>g.positions.get(d.id))}))));
        """)
        by_source = {item['source']: item for item in result}
        self.assertEqual(by_source['pending']['ids'], [2, 17])
        self.assertEqual(by_source['pending']['positions'], [1, 2])
        self.assertEqual(by_source['execution']['ids'], [11, 9])
        self.assertEqual(by_source['execution']['positions'], [1, 2])
        self.assertEqual(by_source['execution']['status'], 'PAUSED')
        self.assertEqual(by_source['review']['ids'], [40])
        self.assertNotIn(50, [draft_id for group in result for draft_id in group['ids']])

    def test_completed_execution_is_not_treated_as_active(self):
        result = self.evaluate("""
          const snapshot={drafts:[{id:1,batch_id:2,queue_position:1,status:'PUBLISHED',filename:'done.mp4'}],
            executions:[{id:7,batch_id:2,status:'COMPLETED'}],
            execution_items:[{execution_id:7,batch_id:2,draft_id:1,execution_position:1}]};
          console.log(JSON.stringify(ui.queueGroups(snapshot)));
        """)
        self.assertEqual(result, [])

    def test_cancelled_batch_drafts_are_absent_from_active_projection(self):
        result = self.evaluate("""
          const snapshot={drafts:[
            {id:4,batch_id:8,queue_position:1,status:'ARCHIVED',filename:'cancelled.mp4'}
          ],executions:[],execution_items:[]};
          console.log(JSON.stringify(ui.queueGroups(snapshot)));
        """)
        self.assertEqual(result, [])

    def test_running_paused_and_ambiguous_drafts_remain_active_in_execution_order(self):
        result = self.evaluate("""
          const drafts=[
            {id:19,batch_id:7,queue_position:1,status:'PREPARING',upload_status:'UPLOAD_STARTED'},
            {id:4,batch_id:7,queue_position:2,status:'MANUAL_ACTION_REQUIRED',upload_status:'TRANSFER_UNKNOWN'},
            {id:30,batch_id:8,queue_position:1,status:'MANUAL_ACTION_REQUIRED',media_status:'NEEDS_RECONCILIATION'}
          ];
          const snapshot={drafts,executions:[
            {id:71,batch_id:8,status:'PAUSED'},
            {id:70,batch_id:7,status:'RUNNING'}
          ],execution_items:[
            {execution_id:70,batch_id:7,draft_id:19,execution_position:2},
            {execution_id:70,batch_id:7,draft_id:4,execution_position:1},
            {execution_id:71,batch_id:8,draft_id:30,execution_position:1}
          ]};
          console.log(JSON.stringify(ui.queueGroups(snapshot).map(group=>({
            status:group.execution?.status,ids:group.drafts.map(draft=>draft.id),positions:group.drafts.map(draft=>group.positions.get(draft.id))
          }))));
        """)
        by_status = {group['status']: group for group in result}
        self.assertEqual(by_status['RUNNING'], {'status': 'RUNNING', 'ids': [4, 19], 'positions': [1, 2]})
        self.assertEqual(by_status['PAUSED'], {'status': 'PAUSED', 'ids': [30], 'positions': [1]})

    def test_ambiguous_remote_result_is_not_presented_as_ready(self):
        transfer = draft_queue_presentation({'status': 'UPLOAD_FAILED', 'upload_status': 'TRANSFER_UNKNOWN'})
        reconcile = draft_queue_presentation({'status': 'MANUAL_ACTION_REQUIRED'},
                                             {'status': 'NEEDS_RECONCILIATION'})
        self.assertIn('Não envie novamente', transfer['message'])
        self.assertEqual(transfer['kind'], 'attention')
        self.assertIn('Verifique o status', reconcile['message'])
        self.assertEqual(reconcile['kind'], 'attention')


if __name__ == '__main__':
    unittest.main()
