#
# {
#   "ArrDelay":5.0,"CRSArrTime":"2015-12-31T03:20:00.000-08:00","CRSDepTime":"2015-12-31T03:05:00.000-08:00",
#   "Carrier":"WN","DayOfMonth":31,"DayOfWeek":4,"DayOfYear":365,"DepDelay":14.0,"Dest":"SAN","Distance":368.0,
#   "FlightDate":"2015-12-30T16:00:00.000-08:00","FlightNum":"6109","Origin":"TUS"
# }
#
from pyspark.sql.types import StringType, IntegerType, FloatType, DoubleType, DateType, TimestampType
from pyspark.sql.types import StructType, StructField
from pyspark.sql.functions import udf

schema = StructType([
  StructField("ArrDelay", DoubleType(), True),     # "ArrDelay":5.0
  StructField("CRSArrTime", TimestampType(), True),    # "CRSArrTime":"2015-12-31T03:20:00.000-08:00"
  StructField("CRSDepTime", TimestampType(), True),    # "CRSDepTime":"2015-12-31T03:05:00.000-08:00"
  StructField("Carrier", StringType(), True),     # "Carrier":"WN"
  StructField("DayOfMonth", IntegerType(), True), # "DayOfMonth":31
  StructField("DayOfWeek", IntegerType(), True),  # "DayOfWeek":4
  StructField("DayOfYear", IntegerType(), True),  # "DayOfYear":365
  StructField("DepDelay", DoubleType(), True),     # "DepDelay":14.0
  StructField("Dest", StringType(), True),        # "Dest":"SAN"
  StructField("Distance", DoubleType(), True),     # "Distance":368.0
  StructField("FlightDate", DateType(), True),    # "FlightDate":"2015-12-30T16:00:00.000-08:00"
  StructField("FlightNum", StringType(), True),   # "FlightNum":"6109"
  StructField("Origin", StringType(), True),      # "Origin":"TUS"
])

features = spark.read.json("data/simple_flight_delay_features.json", schema=schema)
features.first()

#
# Check for nulls in features before using Spark ML
#
null_counts = [(column, features.where(features[column].isNull()).count()) for column in features.columns]
cols_with_nulls = filter(lambda x: x[1] > 0, null_counts)
print(list(cols_with_nulls))

#
# Categorize or 'bucketize' the arrival delay field into on time, or slightly/very late using a DataFrame UDF
#
def bucketize_arr_delay(arr_delay):
  bucket = None
  if arr_delay <= 15.0:
    bucket = 'on_time'
  elif arr_delay > 15.0 and arr_delay <= 60.0:
    bucket = 'slightly_late'
  elif arr_delay > 60.0:
    bucket = 'very_late'
  return bucket

# Wrap the function in pyspark.sql.functions.udf with...
# pyspark.sql.types.StructField information
dummy_function_udf = udf(bucketize_arr_delay, StringType())

# Add a category column via pyspark.sql.DataFrame.withColumn
manual_bucketized_features = features.withColumn(
  "ArrDelayBucket",
  dummy_function_udf(features['ArrDelay'])
)
manual_bucketized_features.select("ArrDelay", "ArrDelayBucket").show()

manual_bucketized_features = manual_bucketized_features.withColumn(
  "DepDelayBucket",
  dummy_function_udf(features['DepDelay'])
)
manual_bucketized_features.select("DepDelay", "DepDelayBucket").show()

#
# Use pysmark.ml.feature.Bucketizer to bucketize ArrDelay into on-time, slightly late, very late (0, 1, 2)
#
from pyspark.ml.feature import Bucketizer

splits = [-float("inf"), 15.0, 60.0, float("inf")]
bucketizer = Bucketizer(
  splits=splits,
  inputCol="ArrDelay",
  outputCol="ArrDelayBucket"
)
ml_bucketized_features = bucketizer.transform(features)

bucketizer = Bucketizer(
  splits=splits,
  inputCol="DepDelay",
  outputCol="DepDelayBucket"
)
ml_bucketized_features = bucketizer.transform(ml_bucketized_features)

ml_bucketized_features.select("ArrDelay", "ArrDelayBucket", "DepDelay", "DepDelayBucket").show()

#
# Extract features tools in with pyspark.ml.feature
#
from pyspark.ml import Pipeline
from pyspark.ml.feature import StringIndexer, OneHotEncoder, VectorIndexer
from pyspark.ml.feature import VectorAssembler

# Turn category fields into categoric feature vectors, then drop intermediate fields
for column in ["Carrier", "DayOfMonth", "DayOfWeek", "DayOfYear",
               "Origin", "Dest", "FlightNum", "DepDelayBucket"]:
  string_indexer = StringIndexer(
    inputCol=column,
    outputCol=column + "_index"
  )
  one_hot_encoder = OneHotEncoder(
    dropLast=False,
    inputCol=column + "_index",
    outputCol=column + "_vec"
  )
  string_pipeline = Pipeline(stages=[string_indexer, one_hot_encoder])
  ml_bucketized_features = string_pipeline.fit(ml_bucketized_features)\
                                          .transform(ml_bucketized_features)
  ml_bucketized_features = ml_bucketized_features.drop(column).drop(column + "_index")

# Handle continuous, numeric fields by combining them into one feature vector
numeric_columns = ["DepDelay", "Distance"]
vector_assembler = VectorAssembler(
  inputCols=numeric_columns,
  outputCol="NumericFeatures_vec"
)
ml_bucketized_features = vector_assembler.transform(ml_bucketized_features)
for column in numeric_columns:
  ml_bucketized_features = ml_bucketized_features.drop(column)

# Combine various features into one feature vector, 'features'
feature_columns = ["Carrier_vec", "DayOfMonth_vec", "DayOfWeek_vec", "DayOfYear_vec",
                   "Origin_vec", "Dest_vec", "FlightNum_vec", "DepDelayBucket_vec",
                   "NumericFeatures_vec"]
assembler = VectorAssembler(
    inputCols=feature_columns,
    outputCol="Features_vec"
)
final_vectorized_features = assembler.transform(ml_bucketized_features)
for column in feature_columns:
  final_vectorized_features = final_vectorized_features.drop(column)

final_vectorized_features.show()

#
# Cross validate, train and evaluate classifier
#

# Test/train split
training_data, test_data = final_vectorized_features.randomSplit([0.8, 0.2])

# Instantiate and fit random forest classifier
from pyspark.ml.classification import RandomForestClassifier
rfc = RandomForestClassifier(featuresCol="Features_vec", labelCol="ArrDelayBucket")
model = rfc.fit(training_data)

# Evaluate model using test data
predictions = model.transform(test_data)

from pyspark.ml.evaluation import MulticlassClassificationEvaluator
evaluator = MulticlassClassificationEvaluator(labelCol="ArrDelayBucket", metricName="accuracy")
accuracy = evaluator.evaluate(predictions)
print("Accuracy = {}".format(mae))

# Show a sample sorted by scheduled departure... needed because prediction is sorted by ArrDelay,
# so show() shows only 0 predictions.
one_percent = predictions.sample(fraction=0.01, withReplacement=True)
one_percent.orderBy("CRSDepTime").show(100)
