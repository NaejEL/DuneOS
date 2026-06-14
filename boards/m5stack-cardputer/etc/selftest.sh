# DuneOS CLI self-test — run with:  source /flash/etc/selftest.sh
# Exercises the coreutils + the scripting shell (vars, $?, pipes, redirection,
# globs, &&/||, if/for/while, test). No arithmetic ($((...))) yet — for loops
# over explicit word lists are the iteration workhorse.

echo "=== DuneOS CLI self-test ==="

# Use /sd (real FAT directories). /tmp is a FLAT namespace: mkdir there makes
# no traversable tree, so find, glob and test -d cannot walk it.
D=/sd/selftest
rm -rf $D
mkdir -p $D/sub

echo "--- write / append / cat ---"
echo "alpha"  > $D/a.txt
echo "beta"  >> $D/a.txt
echo "gamma" >> $D/a.txt
cat $D/a.txt

echo "--- head -n 2 ---"
head -n 2 $D/a.txt

echo "--- grep beta ---"
grep beta $D/a.txt

echo "--- pipe: cat | grep -c a ---"
cat $D/a.txt | grep -c a

echo "--- sed s/beta/BETA/ ---"
sed 's/beta/BETA/' $D/a.txt

echo "--- cp + find -name ---"
cp $D/a.txt $D/sub/b.txt
find $D -name "*.txt"

echo "--- glob: ls \$D/*.txt ---"
ls $D/*.txt

echo "--- redirect to file ---"
grep gamma $D/a.txt > $D/g.txt
cat $D/g.txt

echo "--- touch / wc / du / df / tail ---"
touch $D/t.txt && echo "  touch ok"
wc $D/a.txt
du -h $D
df -h
echo "[tail -n 1]" ; tail -n 1 $D/a.txt

echo "--- mv ---"
echo moveme > $D/m1.txt
mv $D/m1.txt $D/m2.txt
cat $D/m2.txt

echo "--- system tools (smoke: must not crash / show usage) ---"
free
ps
ifconfig
gpio info
services
battery
ping 127.0.0.1 1

echo "--- presence: every tool ---"
for t in ls cat cp head tail touch wc du df grep sed find mkdir rm mv free ps gpio klog battery ifconfig ping services restart reboot input; do
  if test -f /flash/bin/$t.dap; then echo "  $t: ok"; else echo "  $t: MISSING"; fi
done

echo "--- vars + && / || ---"
X=hello
echo "X=$X  status=$?"
test -d $D && echo "  dir exists"
test -e /nope || echo "  /nope absent (expected)"

echo "--- subst / math / break / continue ---"
echo "  subst: $(echo hello world)"
echo "  math: $((2 + 3 * 4))"
i=0
while test $i -lt 9; do i=$((i + 1)); if test $i -eq 3; then break; fi; echo "  loop $i"; done
for n in 1 2 3; do if test $n -eq 2; then continue; fi; echo "  n=$n"; done

echo "--- while (runs once via a flag file) ---"
echo flag > $D/flag
while test -f $D/flag; do echo "  while ran"; rm -f $D/flag; done

echo "--- cleanup: rm -rf ---"
rm -rf $D
test -d $D || echo "  cleaned"

echo "=== done ==="
