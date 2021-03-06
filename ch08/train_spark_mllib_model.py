# !/usr/bin/env python

import sys, os, re
import json
import datetime, iso8601

# Pass date and base path to main() from airflow
def main(iso_date, base_path):
  
  APP_NAME = "train_spark_mllib_model.py"
  
  # If there is no SparkSession, create the environment
  try:
    sc and spark
  except NameError as e:
    import findspark
    findspark.init()
    import pyspark
    import pyspark.sql
    
    sc = pyspark.SparkContext()
    spark = pyspark.sql.SparkSession(sc).builder.appName(APP_NAME).getOrCreate()

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
  
  input_path = "{}/data/simple_flight_delay_features.json".format(
    base_path
  )
  features = spark.read.json(input_path, schema=schema)
  features.first()
  
  #
  # Check for nulls in features before using Spark ML
  #
  null_counts = [(column, features.where(features[column].isNull()).count()) for column in features.columns]
  cols_with_nulls = filter(lambda x: x[1] > 0, null_counts)
  print(list(cols_with_nulls))
  
  #
  # Use pysmark.ml.feature.Bucketizer to bucketize ArrDelay into on-time, slightly late, very late (0, 1, 2)
  #
  from pyspark.ml.feature import Bucketizer
  
  splits = [-float("inf"), 15.0, 60.0, float("inf")]
  arrival_bucketizer = Bucketizer(
    splits=splits,
    inputCol="ArrDelay",
    outputCol="ArrDelayBucket"
  )
  ml_bucketized_features = arrival_bucketizer.transform(features)
  arrival_bucketizer_path = "{}/models/arrival_bucketizer.bin".format(base_path)
  arrival_bucketizer.write().overwrite().save(arrival_bucketizer_path)
  
  departure_bucketizer = Bucketizer(
    splits=splits,
    inputCol="DepDelay",
    outputCol="DepDelayBucket"
  )
  ml_bucketized_features = departure_bucketizer.transform(ml_bucketized_features)
  departure_bucketizer_path = "{}/models/departure_bucketizer.bin".format(base_path)
  departure_bucketizer.write().overwrite().save(departure_bucketizer_path)
  
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
    
    string_pipeline_model = string_pipeline.fit(ml_bucketized_features)
    ml_bucketized_features = string_pipeline_model.transform(ml_bucketized_features)
    
    ml_bucketized_features = ml_bucketized_features.drop(column).drop(column + "_index")
    
    # Save the pipeline model
    string_pipeline_output_path = "{}/models/string_indexer_pipeline_model_{}.bin".format(
      base_path,
      column
    )
    string_pipeline_model.write().overwrite().save(string_pipeline_output_path)
  
  # Handle continuous, numeric fields by combining them into one feature vector
  numeric_columns = ["DepDelay", "Distance"]
  vector_assembler = VectorAssembler(
    inputCols=numeric_columns,
    outputCol="NumericFeatures_vec"
  )
  ml_bucketized_features = vector_assembler.transform(ml_bucketized_features)
  
  # Save the numeric vector assembler
  vector_assembler_path = "{}/models/numeric_vector_assembler.bin".format(base_path)
  vector_assembler.write().overwrite().save(vector_assembler_path)
  
  # Drop the original columns
  for column in numeric_columns:
    ml_bucketized_features = ml_bucketized_features.drop(column)
  
  # Combine various features into one feature vector, 'features'
  feature_columns = ["Carrier_vec", "DayOfMonth_vec", "DayOfWeek_vec", "DayOfYear_vec",
                     "Origin_vec", "Dest_vec", "FlightNum_vec", "DepDelayBucket_vec",
                     "NumericFeatures_vec"]
  final_assembler = VectorAssembler(
      inputCols=feature_columns,
      outputCol="Features_vec"
  )
  final_vectorized_features = final_assembler.transform(ml_bucketized_features)
  for column in feature_columns:
    final_vectorized_features = final_vectorized_features.drop(column)
  
  # Save the final assembler
  final_assembler_path = "{}/models/final_vector_assembler.bin".format(base_path)
  final_assembler.write().overwrite().save(final_assembler_path)
  
  # Inspect the finalized features
  final_vectorized_features = final_vectorized_features.limit(100000) # remove me, I am for the author's development
  final_vectorized_features.show()
  
  # Instantiate and fit random forest classifier on all the data
  from pyspark.ml.classification import RandomForestClassifier
  rfc = RandomForestClassifier(featuresCol="Features_vec", labelCol="ArrDelayBucket", predictionCol="Prediction")
  model = rfc.fit(final_vectorized_features)
  
  # Save the new model over the old one
  model_output_path = "{}/models/spark_random_forest_classifier.flight_delays.bin".format(
    base_path
  )
  model.write().overwrite().save(model_output_path)

if __name__ == "__main__":
  main(sys.argv[1], sys.argv[2])
