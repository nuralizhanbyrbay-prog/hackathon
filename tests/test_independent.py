import copy
import unittest
from unittest.mock import patch
from auditlab.generator import generate,convert
from auditlab.independent import rational_minor,validate_episode
from auditlab.heldout import configurations

class IndependentChecks(unittest.TestCase):
    def test_hand_calculated_values(self):
        self.assertEqual(rational_minor(1,'0.5'),0)
        self.assertEqual(rational_minor(3,'0.5'),2)
        self.assertEqual(rational_minor(-3,'0.5'),-2)
        self.assertEqual(rational_minor(100,'3.1',0),31000)
        self.assertEqual(rational_minor(1000,'1550',3),155000)
        self.assertEqual(rational_minor(99999,'480.4'),48039520)
    def test_new_combinations_independently_valid(self):
        for case in configurations():
            with self.subTest(seed=case['seed']):self.assertIn('issues_checked',validate_episode(generate(**case)))
    def test_detect_shared_conversion_bug(self):
        with patch('auditlab.generator.convert',side_effect=lambda *a:convert(*a)+1):e=generate(1)
        with self.assertRaises(ValueError):validate_episode(e)
    def test_detect_wrong_truth_amount(self):
        e=generate(2);e['truth'][0]['expected_amount_minor']+=1
        with self.assertRaises(ValueError):validate_episode(e)
    def test_reconstructs_each_proof_without_other_records(self):
        e=generate(3,{'interaction':'fx_cutoff'})
        self.assertEqual(validate_episode(e)['evidence_sets_checked'],len(e['truth']))
    def test_reject_insufficient_proof_even_if_id_exists(self):
        e=generate(4);e['truth'][0]['evidence_sets']=[[e['truth'][0]['evidence_sets'][0][0]]]
        with self.assertRaises(ValueError):validate_episode(e)
    def test_reject_foreign_proof(self):
        e=generate(5);e['truth'][0]['evidence_sets']=copy.deepcopy(e['truth'][1]['evidence_sets'])
        with self.assertRaises(ValueError):validate_episode(e)
    def test_reject_broken_line_items(self):
        e=generate(1);e['provenance']['clean']['invoices'][0]['line_items'][0]['total_minor']+=1
        with self.assertRaises(ValueError):validate_episode(e)
    def test_reject_dangling_reference(self):
        e=generate(1);e['provenance']['clean']['payments'][0]['invoice_refs'][0]['invoice_id']='missing'
        with self.assertRaises(ValueError):validate_episode(e)

if __name__=='__main__':unittest.main()
