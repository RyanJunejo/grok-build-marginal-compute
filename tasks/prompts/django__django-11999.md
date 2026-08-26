You are working in /testbed, a checkout of django/django at a fixed commit with its test environment already installed (conda env `testbed`). If `python` or `pytest` is not on PATH, use /opt/miniconda3/envs/testbed/bin/python or run `source /opt/miniconda3/bin/activate && conda activate testbed` first.

Fix the issue described below.

Requirements:
- Modify the repository source code to resolve the issue.
- Run relevant existing tests to verify your fix before finishing.
- Do NOT modify any test files.

<issue>
Cannot override get_FOO_display() in Django 2.2+.
Description
	
I cannot override the get_FIELD_display function on models since version 2.2. It works in version 2.1.
Example:
class FooBar(models.Model):
	foo_bar = models.CharField(_("foo"), choices=[(1, 'foo'), (2, 'bar')])
	def __str__(self):
		return self.get_foo_bar_display() # This returns 'foo' or 'bar' in 2.2, but 'something' in 2.1
	def get_foo_bar_display(self):
		return "something"
What I expect is that I should be able to override this function.
</issue>
