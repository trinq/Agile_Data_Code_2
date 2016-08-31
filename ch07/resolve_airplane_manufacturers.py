airplanes = sqlContext.read.json('data/airplanes.json')

airplanes.registerTempTable("airplanes")
manufacturer_variety = sqlContext.sql(
"""SELECT
  DISTINCT(Manufacturer) AS Manufacturer
FROM
  airplanes
ORDER BY
  Manufacturer"""
)
manufacturer_variety_local = manufacturer_variety.collect()

# We need to print these left justified
for mfr in manufacturer_variety_local:
  print mfr.Manufacturer

# Detect the longest common beginning string in a pair of strings
def longest_common_beginning(s1, s2):
  if s1 == s2:
    return s1
  min_length = min(len(s1), len(s2))
  i = 0
  while i < min_length:
    if s1[i] == s2[i]:
      i += 1
    else:
      break
  return s1[0:i]

# Compare two manufacturers, returning a tuple describing the result
def compare_manufacturers(mfrs):
  mfr1 = mfrs[0]
  mfr2 = mfrs[1]
  lcb = longest_common_beginning(mfr1, mfr2)
  len_lcb = len(lcb)
  record = {
    'mfr1': mfr1,
    'mfr2': mfr2,
    'lcb': lcb,
    'len_lcb': len_lcb,
    'eq': mfr1 == mfr2
  }
  return record

# Pair every unique instance of Manufacturer field with every other for comparison
comparison_pairs = manufacturer_variety.join(manufacturer_variety)

# Do the comparisons
comparisons = comparison_pairs.rdd.map(compare_manufacturers)

# Matches have > 5 starting chars in common
matches = comparisons.filter(lambda f: f['eq'] == False and f['len_lcb'] > 5)

#
# Now we create a mapping of duplicate keys from their raw value to the one we're going to use
#

# 1) Group the matches by the longest common beginning ('lcb')
common_lcbs = matches.groupBy(lambda x: x['lcb'])

# 2) Emit the raw value for each side of the match along with the key, our 'lcb'
mfr1_map = common_lcbs.map(lambda x: [(y['mfr1'], x[0]) for y in x[1]]).flatMap(lambda x: x)
mfr2_map = common_lcbs.map(lambda x: [(y['mfr2'], x[0]) for y in x[1]]).flatMap(lambda x: x)

# 3) Combine the two sides of the comparison's records
map_with_dupes = mfr1_map.union(mfr2_map)

# 4) Remove duplicates
mfr_dedupe_mapping = map_with_dupes.distinct()

