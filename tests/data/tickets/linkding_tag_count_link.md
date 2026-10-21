Tag management: the bookmark count does not open the bookmarks of that tag

Steps to reproduce:
1. Go to /login/
2. Enter {{env:QAX_VAR_LD_USER}} in the Username field
3. Enter {{env:QAX_VAR_LD_PASSWORD}} in the Password field
4. Click "Login"
5. Go to /tags?search=qaxticket
6. Click "1" in the "qaxticket" row

Expected: the bookmarks page opens filtered by the tag (URL /bookmarks?q=%23qaxticket)
Actual: nothing happens; the page shows "Content missing"
