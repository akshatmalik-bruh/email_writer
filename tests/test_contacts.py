import unittest
from backend.contacts import resolve, salutation_for

class ContactTests(unittest.TestCase):
    def setUp(self):
        self.contacts=[{"id":1,"name":"Rajesh Sharma","aliases":"Sharma ji, Rajesh ji","email":"r@example.com"},{"id":2,"name":"Rakesh Shah","aliases":"Sharma ji","email":"s@example.com"}]

    def test_alias_matches(self):
        self.assertEqual(resolve("Sharma ji ko mail",self.contacts)[0]["matched_alias"],"Sharma ji")

    def test_no_match(self): self.assertEqual(resolve("hello",self.contacts),[])

    def test_salutation(self): self.assertEqual(salutation_for("Rajesh Sharma"),"Dear Sharma")

    def test_empty_name(self): self.assertEqual(salutation_for(""),"Dear Sir/Madam")

if __name__ == "__main__": unittest.main()
